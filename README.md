# Bot Telegram Pencari Jawaban

Bot Telegram untuk mencari dan menambahkan soal serta jawaban ke database BigQuery.

## Fitur

- Mencari jawaban dari pertanyaan teks
- Mencari jawaban dari gambar (OCR)
- Menambah soal dan jawaban ke database
- Memproses file CSV/Excel untuk menambah data dalam jumlah banyak

## Cara Menjalankan di Railway

1. Fork repository ini ke GitHub account Anda
2. Buat akun Railway jika belum punya
3. Hubungkan Railway dengan repository GitHub Anda
4. Tambahkan environment variables di Railway:
   - `TELEGRAM_BOT_TOKEN`: Token bot Telegram dari @BotFather
   - `SERVICE_ACCOUNT_JSON`: JSON credentials Google Cloud Service Account
   - `PROJECT_ID`: Google Cloud Project ID (opsional)
   - `DATASET_ID`: BigQuery Dataset ID (opsional)
   - `TABLE_ID`: BigQuery Table ID (opsional)

5. Deploy otomatis akan dilakukan oleh Railway

## Environment Variables

| Variable | Deskripsi | Contoh |
|----------|-----------|--------|
| TELEGRAM_BOT_TOKEN | Token bot Telegram | 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 |
| SERVICE_ACCOUNT_JSON | JSON service account Google Cloud | {"type": "service_account", ...} |
| PROJECT_ID | Google Cloud Project ID | my-project-id |
| DATASET_ID | BigQuery Dataset ID | my_dataset |
| TABLE_ID | BigQuery Table ID | banksoal |

## Cara Penggunaan

1. Mulai bot dengan perintah `/start`
2. Untuk mencari jawaban, ketik pertanyaan langsung
3. Untuk mencari jawaban dari gambar, kirim gambar berisi pertanyaan
4. Untuk menambah soal, gunakan `/tambah [soal] | [jawaban]`
5. Untuk memproses file, kirim file CSV/Excel dengan kolom soal dan jawaban

## Struktur Database

Tabel BigQuery harus memiliki skema berikut:

| Kolom | Tipe | Deskripsi |
|-------|------|-----------|
| soal | STRING | Pertanyaan |
| jawaban | STRING | Jawaban |
| source | STRING | Sumber data |
| created_at | TIMESTAMP | Waktu dibuat |
