import asyncio
import re
import requests
import json
import os  # นำเข้า os เพื่อดึงค่าจาก Secrets
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# ================= CONFIGURATION (ดึงจาก GitHub Secrets) =================
URL = "http://49.0.120.219:99/"
USER = os.getenv("BIO_USER")
PASS = os.getenv("BIO_PASS")
ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
USER_ID = os.getenv("LINE_USER_ID")

# Logic วันที่: ตรวจสอบของ "เมื่อวาน"
now = datetime.now()
yesterday_dt = now - timedelta(days=1)
TARGET_DATE_STR = yesterday_dt.strftime("%d/%m/%Y") 
# =========================================================================

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
        # เน้นการหน่วงเวลาด้วย slow_mo และปรับ timeout เป็น 60 วินาทีเพื่อรองรับเน็ตช้า
        browser = await p.chromium.launch(headless=True) # ต้องเป็น True เมื่อรันบน GitHub
        context = await browser.new_context(viewport={'width': 1366, 'height': 768})
        page = await context.new_page()
        page.set_default_timeout(60000) # รอโหลดแต่ละจุดสูงสุด 60 วิ

        try:
            target_dt = datetime.strptime(TARGET_DATE_STR, "%d/%m/%Y")
            next_day_str = (target_dt + timedelta(days=1)).strftime("%d/%m/%Y")
            from_date_ot = (target_dt - timedelta(days=2)).strftime("%d/%m/%Y")
            to_date_ot = (target_dt + timedelta(days=2)).strftime("%d/%m/%Y")

            # 1. LOGIN (ใช้การรอแบบ networkidle เพื่อให้มั่นใจว่าหน้าเว็บโหลดครบ)
            await page.goto(URL, wait_until="networkidle")
            await page.fill('input[placeholder="Username"]', USER)
            await page.fill('input[placeholder="Password"]', PASS)
            await page.click('button:has-text("Login")')
            
            # รอให้ Dashboard โหลดด้วยการหา Element สำคัญ
            await page.wait_for_selector("small.ng-binding", timeout=30000)
            await asyncio.sleep(5) # หน่วงเวลาเพิ่มความเสถียร

            # 2. ค้นหาข้อมูลกะงาน
            shift_info = "ไม่พบข้อมูล"
            for _ in range(15): # เพิ่มจำนวนรอบการวนหา
                all_smalls = await page.locator("small.ng-binding").all_inner_texts()
                week_text = next((t.strip() for t in all_smalls if any(m in t for m in THAI_MONTHS.keys())), "")
                start_dt, end_dt = parse_thai_week(week_text)
                if start_dt and end_dt:
                    target_floor = target_dt.replace(hour=0, minute=0, second=0)
                    if start_dt <= target_floor <= end_dt: break
                    elif target_floor < start_dt: await page.click('button[ng-click*="pre"]')
                    else: await page.click('button[ng-click*="next"]')
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(3)
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
            # รอจนกว่าตารางจะปรากฏ
            await page.wait_for_selector("table", timeout=30000)
            await asyncio.sleep(5)

            for model, val in [("fromDate", TARGET_DATE_STR), ("toDate", next_day_str)]:
                selector = f'input[ng-model="{model}"]'
                await page.evaluate(f'document.querySelector(\'{selector}\').removeAttribute("readonly")')
                await page.fill(selector, val)
                await page.press(selector, 'Enter')
                await asyncio.sleep(2)

            await page.click('h2:has-text("ตรวจสอบเวลาสแกนนิ้ว")')
            await asyncio.sleep(8) # รอโหลดข้อมูลตาราง

            rows = await page.query_selector_all("table tbody tr")
            raw_times = []
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 5:
                    d, t_in, t_out = (await cols[2].inner_text()).strip(), (await cols[3].inner_text()).strip(), (await cols[4].inner_text()).strip()
                    if ":" in t_in: raw_times.append((d, t_in))
                    if ":" in t_out: raw_times.append((d, t_out))

            # ประมวลผลเวลา
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
                if (is_night and out_h >= 6) or (not is_night and out_h >= 18): is_doing_ot = True

            if is_doing_ot:
                await page.click('a:has-text("บันทึกขอทำโอที")')
                await page.wait_for_selector('button[ng-click*="ChangMode(\'All\')"]')
                await page.click('button[ng-click*="ChangMode(\'All\')"]')
                for model, val in [("fromDate", from_date_ot), ("toDate", to_date_ot)]:
                    selector = f'input[ng-model="{model}"]'
                    await page.evaluate(f'document.querySelector(\'{selector}\').removeAttribute("readonly")')
                    await page.fill(selector, val)
                    await page.press(selector, 'Enter')
                    await asyncio.sleep(2)
                
                await page.select_option('select[name*="_length"]', "100")
                await asyncio.sleep(5)
                ot_rows = await page.query_selector_all("table tbody tr")
                ot_found = False
                for r in ot_rows:
                    if TARGET_DATE_STR in (await r.inner_text()):
                        ot_found = True; break
                ot_status = "✅ มีใบโอทีแล้ว" if ot_found else "❌ ไม่พบใบขอโอที"

            # 5. สรุปรายงาน
            late_status = "✅ ไม่สาย"
            if final_in != "--:--" and safe_to_minutes(final_in) > 480 and not is_night: late_status = "❌ สาย"
            if "วันหยุด" in shift_info or final_in == "--:--": late_status = "➖"

            full_msg = f"{'🌙' if is_night else '☀️'} *{'กะดึก' if is_night else 'กะเช้า'}* | {TARGET_DATE_STR}\n"
            full_msg += f"👍 *เข้า:* {final_in}  👋 *ออก:* {final_out} [{late_status}]\n"
            full_msg += f"🚀 *OT:* {'✅ ✅ ' if '✅' in ot_status else '➖ '}{ot_status}"
            
            if target_dt.day == 17:
                full_msg += "\n\n⚠️ *Note:* วันที่ 17 แล้ว! อย่าลืมเช็ค Biofsoft\n📢 ตรวจสอบความถูกต้องด้วยตนเองอีกครั้ง"

            send_line_notification(full_msg)

        except Exception as e:
            print(f"❌ Error: {e}")
            send_line_notification(f"❌ บอททำงานผิดพลาด: {str(e)[:100]}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_full_bot())
