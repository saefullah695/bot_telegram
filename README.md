# Bank Soal Telegram Bot

Bot Telegram untuk mengelola bank soal dengan integrasi Google BigQuery dan Google Vision OCR.

## Fitur

- Menambahkan soal dan jawaban secara manual
- Import soal dari file CSV/Excel
- OCR untuk mengekstrak soal dari gambar
- Pencarian jawaban dari bank soal
- Integrasi dengan Google BigQuery dan Google Vision API

## Deployment di Railway

Domain: `bottelegram-production-b4c8.up.railway.app`

1. Fork repository ini
2. Buat project baru di Railway
3. Connect dengan repository GitHub Anda
4. Tambahkan environment variables yang diperlukan:
   - `TELEGRAM_BOT_TOKEN`: Token bot Telegram
   - `PROJECT_ID`: Google Cloud Project ID
   - `DATASET_ID`: BigQuery Dataset ID
   - `TABLE_ID`: BigQuery Table ID
   - `SERVICE_ACCOUNT_JSON`: Service account JSON key untuk Google Cloud
5. Deploy aplikasi

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram dari @BotFather |
| `PROJECT_ID` | Google Cloud Project ID |
| `DATASET_ID` | BigQuery Dataset ID |
| `TABLE_ID` | BigQuery Table ID |
| `SERVICE_ACCOUNT_JSON` | Service account JSON key untuk Google Cloud |
| `RAILWAY_PUBLIC_URL` | URL public Railway (otomatis) |
| `PORT` | Port untuk webhook (default: 8443) |

## Command Bot

- `/start` - Memulai bot dan menampilkan panduan
- `/tambah soal=jawaban` - Menambah soal manual
- `/ocr` - Melakukan OCR pada gambar yang dibalas
- Upload file CSV/Excel - Import soal otomatis
- Kirim pertanyaan teks - Mencari jawaban dari bank soal
- Kirim gambar soal - Mencari jawaban dengan OCR

## Struktur Database

Tabel BigQuery harus memiliki schema berikut:

| Field | Type | Mode |
|-------|------|------|
| id | STRING | REQUIRED |
| question | STRING | REQUIRED |
| question_normalized | STRING | REQUIRED |
| answer | STRING | REQUIRED |
| source | STRING | NULLABLE |
| timestamp | TIMESTAMP | REQUIRED |
