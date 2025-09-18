# Bank Soal Telegram Bot

Bot Telegram yang dapat menyimpan dan mencari jawaban dari bank soal menggunakan BigQuery dan Google Vision API.

## Fitur

- Menambahkan soal dan jawaban secara manual
- Mengimpor soal dari file CSV/Excel
- Mengimpor soal dari gambar menggunakan OCR (Optical Character Recognition)
- Mencari jawaban dari database berdasarkan pertanyaan
- Mencari jawaban dari gambar menggunakan OCR

## Cara Setup

### Prasyarat

- Python 3.9+
- Akun Google Cloud dengan:
  - BigQuery API diaktifkan
  - Vision API diaktifkan
  - Service Account JSON file
- Token Bot Telegram

### Environment Variables

Berikut adalah environment variables yang diperlukan:

| Variable | Deskripsi | Contoh |
|----------|-----------|--------|
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram | `7238655260:AAF2EQOI5Zh0MvPzefhNpAZQDzW-I92S3qU` |
| `PROJECT_ID` | ID project Google Cloud | `prime-chess-472020-b6` |
| `DATASET_ID` | ID dataset BigQuery | `bot_telegram_gabung` |
| `TABLE_ID` | ID tabel BigQuery | `banksoal` |
| `SERVICE_ACCOUNT_JSON` | Service account JSON dalam format string | `{"type": "service_account", "project_id": "...", ...}` |
| `RAILWAY_PUBLIC_URL` | URL publik Railway (otomatis dibuat) | `https://your-app.railway.app` |
| `PORT` | Port untuk aplikasi (otomatis dibuat) | `8443` |

### Deploy ke Railway

1. Fork atau clone repository ini
2. Push kode ke GitHub repository
3. Hubungkan repository ke Railway:
   - Login ke Railway
   - Klik "New Project"
   - Pilih "Deploy from GitHub repo"
   - Pilih repository ini
4. Setup environment variables di Railway:
   - Buka tab "Variables"
   - Tambahkan semua environment variables yang diperlukan
   - Untuk `SERVICE_ACCOUNT_JSON`, paste seluruh isi file JSON service account Anda
5. Deploy aplikasi dengan klik "Deploy"

### Cara Penggunaan

#### Commands

- `/start` - Menampilkan pesan selamat datang dan daftar perintah
- `/tambah soal=jawaban` - Menambahkan soal dan jawaban secara manual
- `/ocr` - Melakukan OCR pada gambar yang dibalas (reply)

#### Fitur Lainnya

- **Upload file CSV/Excel** - Bot akan otomatis mendeteksi kolom soal dan jawaban
- **Upload gambar** - Bot akan melakukan OCR dan menyimpan soal-jawaban yang ditemukan
- **Kirim pertanyaan (teks)** - Bot akan mencari jawaban dari database
- **Kirim gambar dengan pertanyaan** - Bot akan melakukan OCR dan mencari jawaban dari database

## Struktur Database BigQuery

Bot menggunakan tabel BigQuery dengan struktur berikut:

```sql
CREATE TABLE `prime-chess-472020-b6.bot_telegram_gabung.banksoal` (
  question STRING,
  question_normalized STRING,
  answer STRING,
  source STRING,
  created_at STRING
);
