# รัน Auto Scanner ด้วย GitHub Actions

Workflow `Scanner` จะทดสอบโค้ดทุกครั้งที่ push และสแกนอัตโนมัติประมาณทุก 5 นาที
โดยแต่ละรอบทำงานครั้งเดียว แล้วเก็บสถานะ cooldown ไว้ใน GitHub Actions cache

## Secrets ที่ต้องตั้งค่า

ไปที่ **Settings → Secrets and variables → Actions → New repository secret** แล้วเพิ่ม:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GEMINI_API_KEY`

ห้าม commit ค่าเหล่านี้ลง repository โดยตรง

## ทดสอบด้วยตนเอง

หลังตั้งค่า Secrets แล้ว ไปที่ **Actions → Scanner → Run workflow**
ระบบจะติดตั้ง dependencies, รันทดสอบทั้งหมด และทำ scan หนึ่งรอบ

หมายเหตุ: GitHub Actions แบบ schedule อาจไม่ได้เริ่มตรงนาทีเป๊ะ โดยเฉพาะช่วงที่ระบบหนาแน่น
