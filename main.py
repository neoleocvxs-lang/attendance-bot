import asyncio
import re
import requests
import json
import os
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# Load .env for local testing
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

IS_GITHUB = "GITHUB_ACTIONS" in os.environ
URL = "http://49.0.120.219:99/"

USER = os.getenv("BIO_USER")
PASS = os.getenv("BIO_PASS")
ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
USER_ID = os.getenv("LINE_USER_ID")

# Logic เลือกวันที่ตรวจสอบ (ถ้าเช้าก่อน 11 โมงให้เช็คของเมื่อวาน)
now = datetime.now()
if now.hour < 11:
    target_dt = now - timedelta(days=1)
else:
    target_dt = now

TARGET_DATE_STR = target_dt.strftime("%d/%m/%Y")

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
}

def send_line_notification(message_text):
    if not ACCESS_TOKEN or not USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"}
    payload = {"to": USER_ID, "messages": [{"type": "text", "text": message_text}]}
    try: requests.post(url, headers=headers, data=json.dumps(payload))
    except: pass

def safe_to_minutes(time_str):
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except: return -1

def minutes_to_str(m_total):
    if m_total < 0: return "--:--"
    return f"{m_total // 60:02d}:{m_total % 60:02d}"

def parse_thai_week(text):
    found_dates = []
    text = text.replace('\xa0', ' ')
    for m_name, m_val in THAI_MONTHS.items():
        if m_name in text:
            pattern = r'(\d+)\s+' + re.escape(m_name) + r'\s+(\d+)'
            matches = re.findall(pattern, text)
            for d, y in matches:
                year = int(y)
                if year > 2400: year -= 543 
                found_dates.append(datetime(year, m_val, int(d)))
    if len(found_dates) >= 2:
        found_dates.sort()
        return found_dates[0], found_dates[-1]
    return None, None

async def run_full_bot():
    if not USER or not PASS:
        print("❌ Error: Missing Login Credentials")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=IS_GITHUB)
        context = await browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(95000)

        try:
            print(f"🚀 เริ่มทำงานวันที่: {TARGET_DATE_STR}")
            
            # 1. Login
            await page.goto(URL, wait_until="load")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            
            # รอ Dashboard
            try:
                await page.wait_for_selector('small.ng-binding', timeout=60000)
            except:
                print("🔄 ไม่เจอ Dashboard ใน 60 วิ... กำลังลอง Refresh")
                await page.reload()
                await page.wait_for_selector('small.ng-binding', timeout=60000)
            
            await asyncio.sleep(10) # รอให้นิ่งตามรีเควส
            await page.keyboard.press("Escape")

            # 2. ค้นหาสัปดาห์
            for _ in range(12):
                all_smalls = await page.locator("small.ng-binding").all_inner_texts()
                week_text = next((t.strip() for t in all_smalls if any(m in t for m in THAI_MONTHS.keys())), "")
                start_dt, end_dt = parse_thai_week(week_text)
                if start_dt and end_dt:
                    target_floor = target_dt.replace(hour=0, minute=0, second=0)
                    if start_dt <= target_floor <= end_dt: break
                    elif target_floor < start_dt: await page.click('button[ng-click*="pre"]')
                    else: await page.click('button[ng-click*="next"]')
                    await asyncio.sleep(5)
                else: break

            # ดึงข้อมูลกะ
            target_abbr = target_dt.strftime("%a").upper()
            box = page.locator(f"#shiftblock li:has(span:has-text('{target_abbr}'))").first
            shift_info = (await box.inner_text()).replace(target_abbr, "").strip()

            # 3. ดึงเวลาสแกนนิ้ว
            await page.click('span:has-text("ข้อมูลเวลา")')
            await asyncio.sleep(3)
            await page.click('a:has-text("แสดงข้อมูลการบันทึกเวลา")')
            await page.wait_for_selector('h2:has-text("ตรวจสอบเวลาสแกนนิ้ว")')
            await asyncio.sleep(5)

            next_day_str = (target_dt + timedelta(days=1)).strftime("%d/%m/%Y")
            for model, val in [("fromDate", TARGET_DATE_STR), ("toDate", next_day_str)]:
                selector = f'input[ng-model="{model}"]'
                await page.evaluate(f'document.querySelector(\'{selector}\').removeAttribute("readonly")')
                await page.fill(selector, val)
                await page.press(selector, 'Enter')
            
            await page.click('h2:has-text("ตรวจสอบเวลาสแกนนิ้ว")')
            await asyncio.sleep(15) # รอตารางโหลด

            rows = await page.query_selector_all("table tbody tr")
            raw_times = []
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 5:
                    d, t_in, t_out = (await cols[2].inner_text()).strip(), (await cols[3].inner_text()).strip(), (await cols[4].inner_text()).strip()
                    if ":" in t_in: raw_times.append((d, t_in))
                    if ":" in t_out: raw_times.append((d, t_out))

            # --- Smart Filtering Logic ---
            final_in, final_out = "--:--", "--:--"
            is_night = "20:00" in shift_info
            today_minutes = [safe_to_minutes(t) for d, t in raw_times if TARGET_DATE_STR in d]
            next_day_minutes = [safe_to_minutes(t) for d, t in raw_times if next_day_str in d]

            if is_night:
                in_candidates = [m for m in today_minutes if m >= 1020]
                final_in = minutes_to_str(min(in_candidates)) if in_candidates else "--:--"
                out_candidates = [m for m in next_day_minutes if 240 <= m <= 660]
                final_out = minutes_to_str(max(out_candidates)) if out_candidates else "--:--"
            else:
                # กะเช้า: เลือกตัวสุดท้ายในช่วงเช้า (กรณีสแกนซ้ำหลายรอบ)
                in_candidates = [m for m in today_minutes if 360 <= m <= 600]
                final_in = minutes_to_str(max(in_candidates)) if in_candidates else "--:--"
                # เวลาออก: เลือกตัวสุดท้ายของวันหลังบ่ายสาม
                out_candidates = [m for m in today_minutes if m >= 900]
                final_out = minutes_to_str(max(out_candidates)) if out_candidates else "--:--"

            # 4. ตรวจใบ OT
            ot_status = "ไม่ได้ทำ OT"
            if final_out != "--:--":
                out_h = int(final_out.split(":")[0])
                if (is_night and (out_h >= 6 or out_h < 4)) or (not is_night and out_h >= 18):
                    await page.click('a:has-text("บันทึกขอทำโอที")')
                    await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]')
                    await page.click('button[ng-click*="ChangMode(\'All\')"]')
                    await asyncio.sleep(5)
                    ot_rows = await page.query_selector_all("table tbody tr")
                    found_ot = any(TARGET_DATE_STR in (await r.inner_text()) for r in ot_rows)
                    ot_status = "✅ มีใบโอทีแล้ว" if found_ot else "❌ ไม่พบใบขอโอที"

            # 5. สรุปผล (ปรับการแสดงผลวันหยุดตามรีเควส)
            is_holiday_text = any(k in shift_info for k in ["วันหยุด", "พักผ่อน"]) or not (":" in shift_info)
            
            if is_holiday_text:
                msg = f"😴 *วันหยุด/พักผ่อน* | {TARGET_DATE_STR}\n"
                late_status = "😴 พักผ่อน"
            else:
                display_shift = "กะดึก" if is_night else "กะเช้า"
                display_icon = "🌙" if is_night else "☀️"
                late_status = "✅ ไม่สาย"
                if final_in != "--:--" and not is_night and safe_to_minutes(final_in) > 480:
                    late_status = "❌ สาย"
                msg = f"{display_icon} *{display_shift}* | {TARGET_DATE_STR}\n"

            msg += f"👍 *เข้า:* {final_in}  👋 *out:* {final_out} [{late_status}]\n"
            msg += f"🚀 *OT:* {'✅ ✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            send_line_notification(msg)
            print("✉️ แจ้งเตือนสำเร็จ!")

        except Exception as e:
            err_msg = str(e)[:100]
            print(f"❌ Error: {err_msg}")
            send_line_notification(f"❌ บอททำงานผิดพลาด: {err_msg}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
