import asyncio
import re
import requests
import json
import os
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# พยายามโหลดไฟล์ .env สำหรับการรันในเครื่อง (Local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ตรวจสอบสถานที่รัน
IS_GITHUB = "GITHUB_ACTIONS" in os.environ

# ================= CONFIGURATION =================
URL = "http://49.0.120.219:99/"

# ดึงค่าความลับจาก Environment Variables
USER = os.getenv("BIO_USER")
PASS = os.getenv("BIO_PASS")
ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
USER_ID = os.getenv("LINE_USER_ID")

# Logic การเลือกวันที่ตรวจสอบ
now = datetime.now()
if now.hour < 12:
    target_dt = now - timedelta(days=1)  # เช็คของเมื่อวาน
else:
    target_dt = now  # เช็คของวันนี้

TARGET_DATE_STR = target_dt.strftime("%d/%m/%Y")
# =================================================

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
}

def send_line_notification(message_text):
    if not ACCESS_TOKEN or not USER_ID:
        print("⚠️ ข้ามการส่ง LINE: ไม่พบ Token หรือ User ID")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }
    payload = {"to": USER_ID, "messages": [{"type": "text", "text": message_text}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            print("✉️ ส่งแจ้งเตือนสำเร็จ!")
        else:
            print(f"⚠️ LINE ส่งไม่สำเร็จ: {response.text}")
    except Exception as e:
        print(f"❌ Error LINE: {e}")

def safe_to_minutes(time_str):
    try:
        if ":" in time_str:
            h, m = map(int, time_str.split(":"))
            return h * 60 + m
    except: pass
    return -1

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
        found_dates.sort(); return found_dates[0], found_dates[-1]
    return None, None

async def run_full_bot():
    if not USER or not PASS:
        print("❌ Error: ไม่พบข้อมูล Login")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=IS_GITHUB, 
            args=["--disable-save-password-bubble", "--disable-notifications"]
        )
        context = await browser.new_context(viewport={'width': 1366, 'height': 768})
        page = await context.new_page()
        page.set_default_timeout(60000)

        try:
            print(f"🚀 เริ่มตรวจวันที่: {TARGET_DATE_STR}")

            # 1. LOGIN
            await page.goto(URL, wait_until="load")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            await asyncio.sleep(2)
            await page.keyboard.press("Escape")
            await page.wait_for_selector('small.ng-binding', timeout=60000)
            await asyncio.sleep(5)

            # 2. ค้นหาข้อมูลกะงาน (ปรับปรุงใหม่: รองรับการยืมกะในวันหยุด)
            for _ in range(15):
                all_smalls = await page.locator("small.ng-binding").all_inner_texts()
                week_text = next((t.strip() for t in all_smalls if any(m in t for m in THAI_MONTHS.keys())), "")
                start_dt, end_dt = parse_thai_week(week_text)
                if start_dt and end_dt:
                    target_floor = target_dt.replace(hour=0, minute=0, second=0)
                    if start_dt <= target_floor <= end_dt: break
                    elif target_floor < start_dt: await page.click('button[ng-click*="pre"]')
                    else: await page.click('button[ng-click*="next"]')
                    await asyncio.sleep(4)
                else:
                    await page.click('button[ng-click*="pre"]')
                    await asyncio.sleep(4)

            # --- LOGIC: ดึงข้อมูลกะงานแบบ 7 วันเพื่อรองรับวันหยุด ---
            all_days = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
            weekly_shifts = []
            for d_abbr in all_days:
                box = page.locator(f"#shiftblock li:has(span:has-text('{d_abbr}'))").first
                txt = (await box.inner_text()).replace(d_abbr, "").strip()
                weekly_shifts.append({"day": d_abbr, "info": txt})

            target_abbr = target_dt.strftime("%a").upper()
            day_info = next((i["info"] for i in weekly_shifts if i["day"] == target_abbr), "")
            
            # ตัดสินใจ: ถ้าไม่มีตัวเลขเวลา ให้ยืมกะแรกของสัปดาห์ที่มีเวลามาใช้
            if not (":" in day_info and any(c.isdigit() for c in day_info)):
                shift_info = next((i["info"] for i in weekly_shifts if ":" in i["info"]), "ไม่พบข้อมูล")
            else:
                shift_info = day_info

            # 3. ดึงข้อมูลสแกนนิ้ว
            await page.click('span:has-text("ข้อมูลเวลา")')
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
            await asyncio.sleep(10)

            rows = await page.query_selector_all("table tbody tr")
            raw_times = []
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 5:
                    d, t_in, t_out = (await cols[2].inner_text()).strip(), (await cols[3].inner_text()).strip(), (await cols[4].inner_text()).strip()
                    if ":" in t_in: raw_times.append((d, t_in))
                    if ":" in t_out: raw_times.append((d, t_out))

            final_in, final_out = "--:--", "--:--"
            is_night = "20:00" in shift_info
            if is_night:
                in_c = [t for d, t in raw_times if TARGET_DATE_STR in d and safe_to_minutes(t) >= 1020]
                final_in = in_c[0] if in_c else "--:--"
                out_c = [t for d, t in raw_times if next_day_str in d and 240 <= safe_to_minutes(t) <= 600]
                final_out = out_c[0] if out_c else "--:--"
            else:
                in_c = [t for d, t in raw_times if TARGET_DATE_STR in d and 360 <= safe_to_minutes(t) <= 600]
                final_in = in_c[0] if in_c else "--:--"
                if final_in != "--:--":
                    out_candidates = [t for d, t in raw_times if TARGET_DATE_STR in d and safe_to_minutes(t) > (safe_to_minutes(final_in) + 30)]
                    final_out = out_candidates[-1] if out_candidates else "--:--"

            # 4. ตรวจสอบใบ OT (ฟิกช่องที่ 5 - Index 4)
            ot_status = "ไม่ได้ทำ OT"
            is_doing_ot = False
            if final_out != "--:--":
                out_h = int(final_out.split(":")[0])
                if (is_night and (out_h >= 6 or out_h < 4)) or (not is_night and out_h >= 18): 
                    is_doing_ot = True

            if is_doing_ot:
                await page.click('a:has-text("บันทึกขอทำโอที")')
                await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]')
                await page.click('button[ng-click*="ChangMode(\'All\')"]')
                await asyncio.sleep(3)
                
                ot_found = False
                ot_rows = await page.query_selector_all("table tbody tr")
                for row in ot_rows:
                    cols = await row.query_selector_all("td")
                    if len(cols) >= 5:
                        work_date_col5 = (await cols[4].inner_text()).strip()
                        if TARGET_DATE_STR in work_date_col5:
                            ot_found = True
                            break
                ot_status = "✅ มีใบโอทีแล้ว" if ot_found else "❌ ไม่พบใบขอโอที"

            # 5. สรุปผล
            is_holiday_label = any(kw in day_info for kw in ["วันหยุด", "วัน", " - "]) and not (":" in day_info)
            is_holiday = is_holiday_label or final_in == "--:--"
            
            if is_holiday:
                late_status = "😴 วันหยุด/พักผ่อน"
            else:
                if final_in != "--:--" and safe_to_minutes(final_in) > 480 and not is_night:
                    late_status = "❌ สาย"
                else:
                    late_status = "✅ ไม่สาย"

            full_msg = f"{'🌙' if is_night else '☀️'} *{'กะดึก' if is_night else 'กะเช้า'}* | {TARGET_DATE_STR}\n"
            full_msg += f"👍 *เข้า:* {final_in}  👋 *ออก:* {final_out} [{late_status}]\n"
            full_msg += f"🚀 *OT:* {'✅ ✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            if target_dt.day == 17:
                full_msg += "\n\n⚠️ *Note:* วันที่ 17 แล้ว! อย่าลืมเช็ค Biofsoft"

            send_line_notification(full_msg)

        except Exception as e:
            if IS_GITHUB: await page.screenshot(path="error_debug.png", full_page=True)
            print(f"❌ Error Detail: {e}")
            send_line_notification(f"❌ บอททำงานผิดพลาด: {str(e)[:100]}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
