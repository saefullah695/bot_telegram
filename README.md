# Telegram Q&A Bot

Bot Telegram untuk mencari jawaban dari bank soal menggunakan BigQuery dan Google Vision API.

## Fitur
- Mencari jawaban dari pertanyaan teks
- Mencari jawaban dari gambar (OCR)
- Menambah soal dan jawaban ke database
- Memproses file CSV untuk import massal

## Teknologi
- Python
- Google Cloud BigQuery
- Google Cloud Vision API
- Telegram Bot API
- OCR.Space API
- Scikit-learn (untuk TF-IDF)
- Sentence Transformers (untuk embeddings)

## Instalasi
1. Clone repositori ini
2. Buat virtual environment: `python -m venv venv`
3. Aktifkan virtual environment:
   - Windows: `venv\Scripts\activate`
   - Linux/Mac: `source venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Salin `.env.example` ke `.env` dan isi dengan nilai yang sesuai
6. Jalankan bot: `python bot.py`

## Penggunaan
- Kirim pertanyaan langsung ke bot untuk mendapatkan jawaban
- Kirim gambar berisi pertanyaan untuk mendapatkan jawaban melalui OCR
- Gunakan perintah `/tambah [soal] | [jawaban]` untuk menambah soal baru
- Kirim file CSV dengan kolom question dan answer untuk import massal

## Deployment ke Railway
1. Hubungkan repositori GitHub ini ke Railway
2. Atur environment variables di Railway:
   - `PROJECT_ID`
   - `DATASET_ID`
   - `TABLE_ID`
   - `TELEGRAM_BOT_TOKEN`
   - `OCR_SPACE_API_KEY`
   - `SERVICE_ACCOUNT_JSON` (jika menggunakan)
3. Railway akan otomatis mendeploy aplikasi

## Kontribusi
Pull request diterima. Untuk perubahan besar, silakan buka issue terlebih dahulu.

## Lisensi
[MIT](https://choosealicense.com/licenses/mit/)
