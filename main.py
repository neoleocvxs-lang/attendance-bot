import asyncio
import re
import requests
import json
import os
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# ================= CONFIGURATION (ดึงจาก Secrets) =================
URL = "http://49.0.120.219:99/"
USER = os.getenv("BIO_USER", "01750")  # ถ้าไม่มี Secret จะใช้ค่า Default นี้
PASS = os.getenv("BIO_PASS", "01750")
ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "your_token")
USER_ID = os.getenv("LINE_USER_ID", "your_user_id")

# Logic วันที่: ตรวจสอบของ "เมื่อวาน"
now = datetime.now()
yesterday_dt = now - timedelta(days=1)
TARGET_DATE_STR = yesterday_dt.strftime("%d/%m/%Y") 
# =================================================================

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
        if response.status_code != 200:
            print(f"⚠️ LINE ส่งไม่สำเร็จ: {response.text}")
    except Exception as e:
        print(f"❌ Error ระบบส่ง LINE: {e}")

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
        found_dates.sort()
        return found_dates[0], found_dates[-1]
    return None, None

async def run_full_bot():
    async with async_playwright() as p:
        # เปิดแบบ headless สำหรับ GitHub
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1366, 'height': 768})
        page = await context.new_page()
        
        # ตั้งค่าเวลารอพื้นฐานเป็น 60 วินาที (จากเดิม 30)
        page.set_default_timeout(60000)

        try:
            target_dt = datetime.strptime(TARGET_DATE_STR, "%d/%m/%Y")
            next_day_str = (target_dt + timedelta(days=1)).strftime("%d/%m/%Y")
            from_date_ot = (target_dt - timedelta(days=2)).strftime("%d/%m/%Y")
            to_date_ot = (target_dt + timedelta(days=2)).strftime("%d/%m/%Y")

            print(f"🚀 เริ่มตรวจสอบข้อมูลของวันที่: {TARGET_DATE_STR}")

            # 1. LOGIN (เพิ่มการรอแบบเน้นเสถียร)
            await page.goto(URL, wait_until="load")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            
            # รอจนกว่าหน้า Dashboard จะขึ้น (หา Element ที่มั่นใจว่ามาแน่ๆ)
            await page.wait_for_selector('small.ng-binding', timeout=60000)
            await asyncio.sleep(5) # แถมเวลาให้เน็ตหายแกว่ง

            # 2. ค้นหาข้อมูลกะงาน (Dashboard)
            shift_info = "ไม่พบข้อมูล"
            for _ in range(15): # เพิ่มรอบการค้นหา
                # รอให้ตัวหนังสือแสดงผลก่อนดึงค่า
                await page.wait_for_selector("small.ng-binding")
                all_smalls = await page.locator("small.ng-binding").all_inner_texts()
                week_text = next((t.strip() for t in all_smalls if any(m in t for m in THAI_MONTHS.keys())), "")
                start_dt, end_dt = parse_thai_week(week_text)
                
                if start_dt and end_dt:
                    target_floor = target_dt.replace(hour=0, minute=0, second=0)
                    if start_dt <= target_floor <= end_dt: break
                    elif target_floor < start_dt: 
                        await page.click('button[ng-click*="pre"]')
                    else: 
                        await page.click('button[ng-click*="next"]')
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(4) # หน่วงเวลาหลังเปลี่ยนหน้า
                else:
                    await page.click('button[ng-click*="pre"]')
                    await asyncio.sleep(5)

            day_abbr = target_dt.strftime("%a")
            day_box = page.locator(f"#shiftblock li:has(span:has-text('{day_abbr}'))").first
            shift_raw = await day_box.inner_text()
            shift_info = shift_raw.replace(day_abbr.upper(), "").strip()

            # 3. ดึงข้อมูลสแกนนิ้ว
            await page.click('span:has-text("ข้อมูลเวลา")')
            await asyncio.sleep(2)
            await page.click('a:has-text("แสดงข้อมูลการบันทึกเวลา")')
            await page.wait_for_selector('h2:has-text("ตรวจสอบเวลาสแกนนิ้ว")', timeout=60000)
            await asyncio.sleep(5)

            for model, val in [("fromDate", TARGET_DATE_STR), ("toDate", next_day_str)]:
                selector = f'input[ng-model="{model}"]'
                await page.evaluate(f'document.querySelector(\'{selector}\').removeAttribute("readonly")')
                await page.fill(selector, val)
                await page.press(selector, 'Enter')
                await asyncio.sleep(2)

            await page.click('h2:has-text("ตรวจสอบเวลาสแกนนิ้ว")')
            await asyncio.sleep(10) # Biosoft โหลดข้อมูลตารางช้ามาก ต้องรอนานหน่อย

            rows = await page.query_selector_all("table tbody tr")
            raw_times = []
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 5:
                    d = (await cols[2].inner_text()).strip()
                    t_in = (await cols[3].inner_text()).strip()
                    t_out = (await cols[4].inner_text()).strip()
                    if ":" in t_in: raw_times.append((d, t_in))
                    if ":" in t_out: raw_times.append((d, t_out))

            # ประมวลผลเวลาเข้า-ออก
            final_in, final_out = "--:--", "--:--"
            is_night = "20:00" in shift_info
            
            if is_night:
                in_c = [t for d, t in raw_times if TARGET_DATE_STR in d and safe_to_minutes(t) >= 1020]
                if in_c: final_in = in_c[0]
                out_c = [t for d, t in raw_times if next_day_str in d and 240 <= safe_to_minutes(t) <= 600]
                if out_c: final_out = out_c[0]
            else:
                in_c = [t for d, t in raw_times if TARGET_DATE_STR in d and 360 <= safe_to_minutes(t) <= 600]
                if in_c: final_in = in_c[0]
                if final_in != "--:--":
                    out_candidates = [t for d, t in raw_times if TARGET_DATE_STR in d and safe_to_minutes(t) > (safe_to_minutes(final_in) + 30)]
                    if out_candidates: final_out = out_candidates[-1]

            # 4. ตรวจสอบใบ OT
            ot_status = "ไม่ได้ทำ OT"
            is_doing_ot = False
            if final_out != "--:--":
                out_h = int(final_out.split(":")[0])
                if (is_night and (out_h >= 6 or out_h < 4)) or (not is_night and out_h >= 18): 
                    is_doing_ot = True

            if is_doing_ot:
                await page.click('a:has-text("บันทึกขอทำโอที")')
                await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]', timeout=60000)
                await page.click('button[ng-click*="ChangMode(\'All\')"]')
                await asyncio.sleep(3)
                for model, val in [("fromDate", from_date_ot), ("toDate", to_date_ot)]:
                    selector = f'input[ng-model="{model}"]'
                    await page.evaluate(f'document.querySelector(\'{selector}\').removeAttribute("readonly")')
                    await page.fill(selector, val)
                    await page.press(selector, 'Enter')
                
                await page.select_option('select[name*="_length"]', "100")
                await asyncio.sleep(5)
                ot_rows_text = await page.locator("table tbody tr").all_inner_texts()
                ot_found = any(TARGET_DATE_STR in r for r in ot_rows_text)
                ot_status = "✅ มีใบโอทีแล้ว" if ot_found else "❌ ไม่พบใบขอโอที"

            # 5. สรุปผล
            late_status = "✅ ไม่สาย"
            if final_in != "--:--" and safe_to_minutes(final_in) > 480 and not is_night: 
                late_status = "❌ สาย"
            if "วันหยุด" in shift_info or final_in == "--:--": 
                late_status = "➖"

            msg = f"{'🌙' if is_night else '☀️'} *{'กะดึก' if is_night else 'กะเช้า'}* | {TARGET_DATE_STR}\n"
            msg += f"👍 *เข้า:* {final_in}  👋 *ออก:* {final_out} [{late_status}]\n"
            msg += f"🚀 *OT:* {'✅ ✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            if target_dt.day == 17:
                msg += "\n\n⚠️ *Note:* วันที่ 17 แล้ว! อย่าลืมเช็ค Biofsoft"

            send_line_notification(msg)
            print("✅ ดำเนินการเสร็จสิ้น")

        except Exception as e:
            # --- จุดสำคัญ: ถ่ายรูปหน้าจอเมื่อพัง ---
            await page.screenshot(path="error_debug.png", full_page=True)
            error_msg = f"❌ บอททำงานผิดพลาด: {str(e)[:100]}"
            print(error_msg)
            send_line_notification(error_msg)
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
