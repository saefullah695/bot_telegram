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

### Langkah-langkah

1. Clone repository ini
   ```bash
   git clone https://github.com/username/bank-soal-bot.git
   cd bank-soal-bot
