import asyncio
import re
import requests
import json
import os
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# ตรวจสอบสถานที่รัน
IS_GITHUB = "GITHUB_ACTIONS" in os.environ

# ================= CONFIGURATION =================
URL = "http://49.0.120.219:99/"

if IS_GITHUB:
    USER = os.getenv("BIO_USER")
    PASS = os.getenv("BIO_PASS")
    ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
    USER_ID = os.getenv("LINE_USER_ID")
else:
    # --- ใส่ข้อมูลของคุณตรงนี้สำหรับรันในคอมตัวเอง ---
    USER = "01750"
    PASS = "01750"
    ACCESS_TOKEN = "g+SuHToVW2tfe1xaMnCaBpXcntd76+Psu1MXtVUk1wTSpZyRUs6rc2i/iI2kNWC80Rb6Jw7P6rU5P3rAoSXPegM8ijpa8Tr7aOeUr6Is5Kx/Eme3POogYxltROwj6zcT8sJawuFHL89eekAqreHtlgdB04t89/1O/w1cDnyilFU="
    USER_ID = "U3a013094c7297e8b2ba3644e2da65d70"

# Logic การเลือกวันที่ (ใช้เวลาไทย)
# บน GitHub เราจะตั้ง TZ: Asia/Bangkok ใน YAML ทำให้ datetime.now() ตรงกับไทย
now = datetime.now()
if now.hour < 12:
    target_dt = now - timedelta(days=1) # รอบ 10:00 เช็คเมื่อวาน
else:
    target_dt = now # รอบ 17:30, 22:00 เช็ควันนี้

TARGET_DATE_STR = target_dt.strftime("%d/%m/%Y")
# =================================================

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
}

def send_line_notification(message_text):
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
    async with async_playwright() as p:
        # ถ้ารันในคอมจะเปิดหน้าจอ (headless=False) ถ้ารันบน GitHub จะปิดหน้าจอ
        browser = await p.chromium.launch(headless=IS_GITHUB, slow_mo=500 if not IS_GITHUB else 0)
        context = await browser.new_context(viewport={'width': 1366, 'height': 768})
        page = await context.new_page()
        page.set_default_timeout(60000)

        try:
            print(f"🚀 เริ่มตรวจวันที่: {TARGET_DATE_STR} (โหมด: {'GitHub' if IS_GITHUB else 'Local'})")

            # 1. LOGIN
            await page.goto(URL, wait_until="load")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            await page.wait_for_selector('small.ng-binding', timeout=60000)
            await asyncio.sleep(5)

            # 2. ค้นหาข้อมูลกะงาน
            shift_info = "ไม่พบข้อมูล"
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

            day_abbr = target_dt.strftime("%a")
            day_box = page.locator(f"#shiftblock li:has(span:has-text('{day_abbr}'))").first
            shift_raw = await day_box.inner_text()
            shift_info = shift_raw.replace(day_abbr.upper(), "").strip()

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

            # 4. ตรวจสอบใบ OT
            ot_status = "ไม่ได้ทำ OT"
            is_doing_ot = False
            if final_out != "--:--":
                out_h = int(final_out.split(":")[0])
                if (is_night and (out_h >= 6 or out_h < 4)) or (not is_night and out_h >= 18): is_doing_ot = True

            if is_doing_ot:
                await page.click('a:has-text("บันทึกขอทำโอที")')
                await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]')
                await page.click('button[ng-click*="ChangMode(\'All\')"]')
                await asyncio.sleep(3)
                ot_rows_text = await page.locator("table tbody tr").all_inner_texts()
                ot_found = any(TARGET_DATE_STR in r for r in ot_rows_text)
                ot_status = "✅ มีใบโอทีแล้ว" if ot_found else "❌ ไม่พบใบขอโอที"

            # 5. สรุปผล
            late_status = "✅ ไม่สาย"
            if final_in != "--:--" and safe_to_minutes(final_in) > 480 and not is_night: late_status = "❌ สาย"
            if "วันหยุด" in shift_info or final_in == "--:--": late_status = "➖"

            full_msg = f"{'🌙' if is_night else '☀️'} *{'กะดึก' if is_night else 'กะเช้า'}* | {TARGET_DATE_STR}\n"
            full_msg += f"👍 *เข้า:* {final_in}  👋 *ออก:* {final_out} [{late_status}]\n"
            full_msg += f"🚀 *OT:* {'✅ ✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            if target_dt.day == 17:
                full_msg += "\n\n⚠️ *Note:* วันที่ 17 แล้ว! อย่าลืมเช็ค Biofsoft"

            send_line_notification(full_msg)

        except Exception as e:
            if IS_GITHUB: await page.screenshot(path="error_debug.png", full_page=True)
            send_line_notification(f"❌ บอททำงานผิดพลาด: {str(e)[:100]}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
