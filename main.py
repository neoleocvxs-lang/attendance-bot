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

now = datetime.now()
current_hour = now.hour

# --- [ปรับปรุง] Logic เลือกวันที่ตรวจสอบ (จุดตัด 09:00 น.) ---
if current_hour < 9:
    target_dt = now - timedelta(days=1) # ก่อนเก้าโมงเช็คของเมื่อวาน (กะดึกเลิกงาน)
else:
    target_dt = now # หลังเก้าโมงเช็คของวันนี้ (กะเช้าเข้างาน / กะดึกเริ่มคืนนี้)

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
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=IS_GITHUB)
        context = await browser.new_context(viewport={'width': 1366, 'height': 768})
        page = await context.new_page()
        page.set_default_timeout(95000)

        try:
            # 1. Login
            await page.goto(URL, wait_until="load")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            
            try:
                await page.wait_for_selector('small.ng-binding', timeout=60000)
            except:
                await page.reload()
                await page.wait_for_selector('small.ng-binding', timeout=60000)
            
            await asyncio.sleep(10)
            await page.keyboard.press("Escape")

            # 2. ค้นหาสัปดาห์ (Stable Iterator)
            for _ in range(12):
                elements = page.locator("small.ng-binding")
                count = await elements.count()
                
                week_text = ""
                for i in range(count):
                    txt = await elements.nth(i).inner_text()
                    if any(m in txt for m in THAI_MONTHS.keys()):
                        week_text = txt.strip()
                        break
                
                start_dt, end_dt = parse_thai_week(week_text)
                if start_dt and end_dt:
                    target_floor = target_dt.replace(hour=0, minute=0, second=0)
                    if start_dt <= target_floor <= end_dt: break
                    elif target_floor < start_dt: await page.click('button[ng-click*="pre"]')
                    else: await page.click('button[ng-click*="next"]')
                    await asyncio.sleep(5)
                else: break

            target_abbr = target_dt.strftime("%a").upper()
            box = page.locator(f"#shiftblock li:has(span:has-text('{target_abbr}'))").first
            shift_info = (await box.inner_text()).replace(target_abbr, "").strip()
            is_night = "20:00" in shift_info
            is_holiday = any(k in shift_info for k in ["วันหยุด", "พักผ่อน"]) or not (":" in shift_info)

            # --- [ปรับปรุง] Logic การงดส่งข้อความ (Suppress Notification) ---
            if is_night:
                # กะดึก: งดส่งรอบเช้า (10:00) และเย็น (17:00) เพราะไม่มีข้อมูลสำคัญ
                if current_hour in [10, 17]: return
            
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
            await asyncio.sleep(15)

            rows = await page.query_selector_all("table tbody tr")
            raw_times = []
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 5:
                    d, t_in, t_out = (await cols[2].inner_text()).strip(), (await cols[3].inner_text()).strip(), (await cols[4].inner_text()).strip()
                    if ":" in t_in: raw_times.append((d, t_in))
                    if ":" in t_out: raw_times.append((d, t_out))

            final_in, final_out = "--:--", "--:--"
            today_minutes = [safe_to_minutes(t) for d, t in raw_times if TARGET_DATE_STR in d]
            next_day_minutes = [safe_to_minutes(t) for d, t in raw_times if next_day_str in d]

            if is_night:
                in_candidates = [m for m in today_minutes if m >= 1020]
                final_in = minutes_to_str(min(in_candidates)) if in_candidates else "--:--"
                out_candidates = [m for m in next_day_minutes if 240 <= m <= 660]
                final_out = minutes_to_str(max(out_candidates)) if out_candidates else "--:--"
            else:
                in_candidates = [m for m in today_minutes if 360 <= m <= 600]
                final_in = minutes_to_str(max(in_candidates)) if in_candidates else "--:--"
                out_candidates = [m for m in today_minutes if m >= 900]
                final_out = minutes_to_str(max(out_candidates)) if out_candidates else "--:--"

            # --- [ปรับปรุง] Logic งดส่งซ้ำซ้อนสำหรับกะเช้า ---
            if not is_night and not is_holiday:
                # รอบเย็น (17:00): ถ้ายังไม่สแกนออก (อาจทำโอที) ให้รอส่งรอบทุ่ม/สองทุ่ม
                if current_hour == 17 and final_out == "--:--": return
                # รอบค่ำ (20:00): ถ้าออกเวลาปกติ (แจ้งไปแล้วตอนเย็น) ไม่ต้องส่งซ้ำ
                if current_hour == 20 and final_out != "--:--":
                    out_min = safe_to_minutes(final_out)
                    if 990 <= out_min <= 1050: return

            # 4. ตรวจใบ OT (เจาะจงช่องที่ 5)
            ot_status = "ไม่ได้ทำ OT"
            if final_out != "--:--":
                out_h = int(final_out.split(":")[0])
                if (is_night and (out_h >= 6 or out_h < 4)) or (not is_night and out_h >= 18):
                    await page.click('a:has-text("บันทึกขอทำโอที")')
                    await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]')
                    await page.click('button[ng-click*="ChangMode(\'All\')"]')
                    await asyncio.sleep(7) 
                    
                    ot_rows = await page.query_selector_all("table tbody tr")
                    found_ot = False
                    for row in ot_rows:
                        cols = await row.query_selector_all("td")
                        if len(cols) >= 5:
                            date_in_col = (await cols[4].inner_text()).strip()
                            if TARGET_DATE_STR in date_in_col:
                                found_ot = True
                                break
                    ot_status = "✅ มีใบโอทีแล้ว" if found_ot else "❌ ไม่พบใบขอโอที"

            # 5. สรุปผล
            if is_holiday:
                msg = f"😴 *วันหยุด/พักผ่อน* | {TARGET_DATE_STR}\n"
            else:
                display_shift = "กะดึก" if is_night else "กะเช้า"
                display_icon = "🌙" if is_night else "☀️"
                msg = f"{display_icon} *{display_shift}* | {TARGET_DATE_STR}\n"

            msg += f"👍 *เข้า:* {final_in}  👋 *ออก:* {final_out}\n"
            msg += f"🚀 *OT:* {'✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            send_line_notification(msg)

        except Exception as e:
            send_line_notification(f"❌ บอทผิดพลาด: {str(e)[:100]}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
