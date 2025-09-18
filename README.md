# Telegram Bot dengan BigQuery dan Google Vision

Bot Telegram yang dapat:
1. Menyimpan dan mencari jawaban dari database BigQuery
2. Melakukan OCR pada gambar untuk mengekstrak teks
3. Memproses file CSV untuk menambah data soal dan jawaban

## Fitur
- Mencari jawaban dari pertanyaan teks
- Mencari jawaban dari gambar (dengan OCR)
- Menambah soal dan jawaban ke database
- Memproses file CSV untuk menambah data

## Cara Menjalankan di Railway

1. Fork atau clone repositori ini
2. Buat proyek baru di Railway
3. Hubungkan repositori GitHub ke Railway
4. Set environment variables berikut:
   - `TELEGRAM_BOT_TOKEN`: Token bot Telegram
   - `PROJECT_ID`: ID project Google Cloud
   - `DATASET_ID`: ID dataset BigQuery
   - `TABLE_ID`: ID tabel BigQuery
   - `SERVICE_ACCOUNT_JSON`: JSON string dari service account Google Cloud (dalam format JSON, bukan base64)

5. Deploy

## Environment Variables

| Variable | Deskripsi |
|----------|-----------|
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram dari @BotFather |
| `PROJECT_ID` | ID project Google Cloud |
| `DATASET_ID` | ID dataset di BigQuery |
| `TABLE_ID` | ID tabel di BigQuery |
| `SERVICE_ACCOUNT_JSON` | JSON string dari service account |

## Struktur Tabel BigQuery

Tabel di BigQuery harus memiliki struktur sebagai berikut:
- `id`: STRING (nullable) - ID unik otomatis
- `question`: STRING (nullable) - Pertanyaan asli
- `question_normal`: STRING (nullable) - Pertanyaan yang dinormalisasi
- `answer`: STRING (nullable) - Jawaban
- `Source`: STRING (nullable) - Sumber data (otomatis)
- `timestamp`: STRING (nullable) - Timestamp (otomatis)

## Cara Penggunaan Bot

1. **Mencari jawaban**: Ketik pertanyaan langsung ke bot
2. **Mencari jawaban dari gambar**: Kirim gambar yang berisi pertanyaan
3. **Menambah soal**: Gunakan perintah `/tambah [soal] | [jawaban]`
4. **Upload CSV**: Kirim file CSV dengan kolom `question` dan `answer`

## Contoh Perintah
