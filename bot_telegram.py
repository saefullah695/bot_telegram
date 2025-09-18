import os
import re
import io
import json
import logging
import datetime
import pandas as pd
from tempfile import NamedTemporaryFile
from typing import List, Tuple

from google.cloud import bigquery
from google.cloud import vision
from google.cloud.vision import ImageAnnotatorClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Setup logging dengan format yang lebih detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Konfigurasi BigQuery
PROJECT_ID = os.getenv("PROJECT_ID", "prime-chess-472020-b6")
DATASET_ID = os.getenv("DATASET_ID", "bot_telegram_gabung")
TABLE_ID = os.getenv("TABLE_ID", "banksoal")
TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# Global clients
bq_client = None
vision_client = None

# =======================
# 🔑 SETUP BIGQUERY & GOOGLE VISION
# =======================

def initialize_services():
    """Inisialisasi BigQuery dan Google Vision Client"""
    global bq_client, vision_client
    try:
        # Gunakan environment variable untuk service account
        service_account_info = os.getenv("SERVICE_ACCOUNT_JSON")
        if service_account_info:
            # Simpan ke file sementara
            with NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
                json.dump(json.loads(service_account_info), temp_file)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_file.name
        else:
            logger.warning("SERVICE_ACCOUNT_JSON tidak ditemukan di environment variables")
            # Coba gunakan default credentials (untuk environment seperti Railway)
            # Railway akan secara otomatis menyediakan credentials melalui environment variables
        
        # Inisialisasi clients
        bq_client = bigquery.Client(project=PROJECT_ID)
        vision_client = vision.ImageAnnotatorClient()
        logger.info("BigQuery dan Vision clients berhasil diinisialisasi")
        return bq_client, vision_client
    except Exception as e:
        logger.error(f"Gagal menginisialisasi services: {e}")
        raise

# =======================
# ⚙️ FUNGSI UTAMA
# =======================

def simpan_soal(soal: str, jawaban: str, source: str = "manual") -> bool:
    """Simpan soal ke BigQuery (hindari duplikat)"""
    try:
        soal, jawaban = str(soal).strip(), str(jawaban).strip()
        if not soal or not jawaban:
            logger.warning("Soal atau jawaban kosong, tidak disimpan")
            return False

        # Cek duplikat
        query = f"""
        SELECT COUNT(*) as count 
        FROM `{TABLE_REF}` 
        WHERE LOWER(soal) = LOWER('{soal.replace("'", "''")}')
        """
        query_job = bq_client.query(query)
        result = list(query_job.result())[0]
        
        if result.count > 0:
            logger.info("Soal sudah ada di database, tidak disimpan lagi")
            return False

        # Insert data baru
        rows_to_insert = [{
            "soal": soal,
            "jawaban": jawaban,
            "source": source,
            "created_at": datetime.datetime.now().isoformat()
        }]

        errors = bq_client.insert_json(TABLE_REF, rows_to_insert)
        if errors:
            logger.error(f"Error inserting row: {errors}")
            return False
        
        logger.info("Soal berhasil disimpan ke database")
        return True
    except Exception as e:
        logger.error(f"Error menyimpan soal: {e}")
        return False

def parse_qa_text(text: str) -> List[Tuple[str, str]]:
    """Parse teks untuk mengekstrak soal dan jawaban"""
    questions_answers = []
    try:
        # Pattern untuk mendeteksi format Q: dan A:
        pattern = r'(?i)(Q:|Pertanyaan:|Soal:)\s*(.*?)(?=(?:A:|Jawaban:|$))(?:\s*(?:A:|Jawaban:)\s*(.*))?'
        matches = re.findall(pattern, text, re.DOTNAME)
        
        for match in matches:
            question = match[1].strip()
            answer = match[2].strip() if len(match) > 2 and match[2] else ""
            
            if question and answer:
                questions_answers.append((question, answer))
        
        # Jika tidak ada pattern Q:A, coba split dengan baris baru
        if not questions_answers and "\n" in text:
            lines = text.split("\n")
            for i in range(len(lines)-1):
                if lines[i].strip() and lines[i+1].strip():
                    questions_answers.append((lines[i].strip(), lines[i+1].strip()))
    
    except Exception as e:
        logger.error(f"Error parsing teks: {e}")
    
    return questions_answers

def ocr_with_google_vision(image_content: bytes) -> str:
    """Melakukan OCR pada gambar menggunakan Google Cloud Vision API"""
    try:
        image = vision.Image(content=image_content)
        response = vision_client.document_text_detection(image=image)
        
        if response.error.message:
            logger.error(f"Error OCR: {response.error.message}")
            return ""
        
        return response.text_annotations[0].text if response.text_annotations else ""
    except Exception as e:
        logger.error(f"Error dalam OCR: {e}")
        return ""

def find_question_answer_columns(headers: List[str]) -> Tuple[List[int], List[int]]:
    """Mencari indeks kolom yang mengandung 'soal' dan 'jawaban' dalam header"""
    question_indices = []
    answer_indices = []
    
    for i, header in enumerate(headers):
        header_lower = header.lower()
        if any(keyword in header_lower for keyword in ['soal', 'pertanyaan', 'question']):
            question_indices.append(i)
        if any(keyword in header_lower for keyword in ['jawaban', 'answer', 'kunci']):
            answer_indices.append(i)
    
    return question_indices, answer_indices

def find_answer_from_question(question: str) -> str:
    """Mencari jawaban dari database berdasarkan pertanyaan"""
    try:
        # Normalisasi pertanyaan untuk pencarian
        question_normalized = re.sub(r'\s+', ' ', question.lower().strip())
        
        query = f"""
        SELECT jawaban 
        FROM `{TABLE_REF}` 
        WHERE LOWER(REPLACE(soal, ' ', '')) LIKE '%{question_normalized.replace(" ", "").replace("'", "''")}%'
        LIMIT 1
        """
        
        query_job = bq_client.query(query)
        results = list(query_job.result())
        
        return results[0].jawaban if results else "Jawaban tidak ditemukan di database."
    except Exception as e:
        logger.error(f"Error mencari jawaban: {e}")
        return "Terjadi error saat mencari jawaban."

# =======================
# 🤖 TELEGRAM BOT HANDLER
# =======================

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /start")
        
        welcome_text = (
            "Halo! Saya adalah bot pencari jawaban. Saya dapat membantu Anda:\n\n"
            "1. Mencari jawaban dari pertanyaan teks - langsung ketik pertanyaan Anda\n"
            "2. Mencari jawaban dari gambar - kirim gambar berisi pertanyaan\n"
            "3. Menambah soal dan jawaban ke database - gunakan /tambah\n"
            "4. Memproses file Excel/CSV - kirim file tersebut\n\n"
            "Gunakan /help untuk info lebih lanjut."
        )
        
        await update.message.reply_text(welcome_text)
    except Exception as e:
        logger.error(f"Error di command /start: {e}")
        await update.message.reply_text("Terjadi error. Silakan coba lagi nanti.")

# /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /help"""
    try:
        help_text = (
            "📚 BOT PENCARI JAWABAN - BANTUAN\n\n"
            "Perintah yang tersedia:\n"
            "/start - Memulai bot\n"
            "/help - Menampilkan bantuan ini\n"
            "/tambah [soal] | [jawaban] - Menambah soal dan jawaban ke database\n"
            "/ocr - Melakukan OCR pada gambar yang dikirim sebelumnya\n\n"
            "Cara penggunaan:\n"
            "1. Untuk mencari jawaban, ketik langsung pertanyaan Anda\n"
            "2. Untuk mencari jawaban dari gambar, kirim gambar berisi pertanyaan\n"
            "3. Untuk menambah data, gunakan /tambah atau kirim file Excel/CSV"
        )
        
        await update.message.reply_text(help_text)
    except Exception as e:
        logger.error(f"Error di command /help: {e}")
        await update.message.reply_text("Terjadi error. Silakan coba lagi nanti.")

# /tambah
async def tambah_soal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /tambah"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /tambah dengan args: {context.args}")
        
        if not context.args:
            await update.message.reply_text("Format: /tambah [soal] | [jawaban]\nContoh: /tambah Siapa presiden pertama Indonesia? | Soekarno")
            return
        
        # Gabungkan semua args dan split dengan pemisah |
        full_text = " ".join(context.args)
        if "|" not in full_text:
            await update.message.reply_text("Gunakan | untuk memisahkan soal dan jawaban.\nContoh: /tambah Siapa presiden pertama Indonesia? | Soekarno")
            return
        
        parts = full_text.split("|", 1)
        if len(parts) < 2:
            await update.message.reply_text("Format salah. Pastikan ada soal dan jawaban.\nContoh: /tambah Siapa presiden pertama Indonesia? | Soekarno")
            return
        
        soal, jawaban = parts[0].strip(), parts[1].strip()
        
        if simpan_soal(soal, jawaban, f"telegram_{user.id}"):
            await update.message.reply_text("Soal dan jawaban berhasil ditambahkan!")
        else:
            await update.message.reply_text("Gagal menambahkan soal. Mungkin soal sudah ada di database.")
            
    except Exception as e:
        logger.error(f"Error di command /tambah: {e}")
        await update.message.reply_text("Terjadi error. Silakan coba lagi nanti.")

# Cari jawaban dari teks
async def cari_jawaban_teks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari bank soal berdasarkan teks"""
    try:
        user = update.effective_user
        pertanyaan = update.message.text.strip()
        logger.info(f"User {user.username} ({user.id}) mencari jawaban untuk: {pertanyaan}")
        
        # Tampilkan status sedang mencari
        await update.message.reply_chat_action(action="typing")
        
        # Cari jawaban
        jawaban = find_answer_from_question(pertanyaan)
        
        # Kirim jawaban
        await update.message.reply_text(f"Jawaban: {jawaban}")
        
    except Exception as e:
        logger.error(f"Error mencari jawaban teks: {e}")
        await update.message.reply_text("Terjadi error saat mencari jawaban. Silakan coba lagi nanti.")

# Cari jawaban dari gambar
async def cari_jawaban_gambar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari bank soal berdasarkan gambar"""
    try:
        user = update.effective_user
        photo = update.message.photo[-1]  # Ambil resolusi tertinggi
        file_id = photo.file_id
        file_size = photo.file_size
        logger.info(f"User {user.username} ({user.id}) mencari jawaban dari gambar dengan ID: {file_id} ({file_size} bytes)")
        
        # Tampilkan status sedang memproses
        await update.message.reply_chat_action(action="typing")
        
        # Download gambar
        file = await context.bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
        
        # Lakukan OCR
        teks_hasil_ocr = ocr_with_google_vision(bytes(file_bytes))
        
        if not teks_hasil_ocr:
            await update.message.reply_text("Tidak dapat membaca teks dari gambar. Pastikan gambar jelas dan berisi teks.")
            return
        
        # Cari jawaban berdasarkan teks hasil OCR
        jawaban = find_answer_from_question(teks_hasil_ocr)
        
        # Kirim hasil
        await update.message.reply_text(f"Teks terdeteksi: {teks_hasil_ocr}\n\nJawaban: {jawaban}")
        
    except Exception as e:
        logger.error(f"Error mencari jawaban gambar: {e}")
        await update.message.reply_text("Terjadi error saat memproses gambar. Silakan coba lagi nanti.")

# Command untuk OCR
async def ocr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /ocr"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /ocr")
        
        # Cek apakah ada gambar yang dikirim sebelumnya
        if not context.args or not context.args[0].startswith("file_id:"):
            await update.message.reply_text("Kirim gambar terlebih dahulu, lalu reply dengan /ocr")
            return
        
        # Ekstrak file_id dari argumen
        file_id = context.args[0].replace("file_id:", "")
        
        # Download gambar
        file = await context.bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
        
        # Lakukan OCR
        teks_hasil_ocr = ocr_with_google_vision(bytes(file_bytes))
        
        if not teks_hasil_ocr:
            await update.message.reply_text("Tidak dapat membaca teks dari gambar.")
            return
        
        await update.message.reply_text(f"Hasil OCR:\n\n{teks_hasil_ocr}")
        
    except Exception as e:
        logger.error(f"Error di command /ocr: {e}")
        await update.message.reply_text("Terjadi error saat melakukan OCR. Silakan coba lagi nanti.")

# Upload file (CSV/Excel/Gambar)
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk upload file"""
    try:
        user = update.effective_user
        file = update.message.document
        filename = file.file_name
        file_size = file.file_size
        logger.info(f"User {user.username} ({user.id}) mengupload file: {filename} ({file_size} bytes)")
        
        # Tampilkan status sedang memproses
        await update.message.reply_chat_action(action="typing")
        
        # Download file
        file_obj = await context.bot.get_file(file.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        # Proses berdasarkan tipe file
        if filename.endswith(('.csv', '.xlsx', '.xls')):
            # Proses file CSV/Excel
            try:
                if filename.endswith('.csv'):
                    df = pd.read_csv(io.BytesIO(file_bytes))
                else:
                    df = pd.read_excel(io.BytesIO(file_bytes))
                
                # Cari kolom soal dan jawaban
                question_cols, answer_cols = find_question_answer_columns(df.columns.tolist())
                
                if not question_cols or not answer_cols:
                    await update.message.reply_text("Tidak dapat menemukan kolom soal dan jawaban. Pastikan file memiliki kolom dengan nama 'soal' dan 'jawaban'.")
                    return
                
                # Simpan data ke database
                count_success = 0
                for _, row in df.iterrows():
                    soal = str(row.iloc[question_cols[0]]) if question_cols else ""
                    jawaban = str(row.iloc[answer_cols[0]]) if answer_cols else ""
                    
                    if soal and jawaban and simpan_soal(soal, jawaban, f"file_{filename}"):
                        count_success += 1
                
                await update.message.reply_text(f"File berhasil diproses. {count_success} soal ditambahkan ke database.")
                
            except Exception as e:
                logger.error(f"Error memproses file: {e}")
                await update.message.reply_text("Gagal memproses file. Pastikan format file benar.")
        
        else:
            # Proses sebagai gambar (OCR)
            teks_hasil_ocr = ocr_with_google_vision(bytes(file_bytes))
            
            if not teks_hasil_ocr:
                await update.message.reply_text("Tidak dapat membaca teks dari file. Pastikan file berisi teks yang jelas.")
                return
            
            await update.message.reply_text(f"Hasil OCR:\n\n{teks_hasil_ocr}")
            
    except Exception as e:
        logger.error(f"Error handling file: {e}")
        await update.message.reply_text("Terjadi error saat memproses file. Silakan coba lagi nanti.")

# Handler untuk error
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk error"""
    logger.error(f"Exception while handling an update: {context.error}")
    if update and update.message:
        await update.message.reply_text("Terjadi error. Silakan coba lagi nanti.")

# =======================
# 🚀 MAIN
# =======================

def main():
    """Fungsi utama untuk menjalankan bot"""
    try:
        # Token bot dari environment variable
        TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        if not TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables")
            return
        
        # Inisialisasi services
        initialize_services()
        
        # Buat application dan tambahkan handlers
        application = Application.builder().token(TOKEN).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("tambah", tambah_soal))
        application.add_handler(CommandHandler("ocr", ocr_command))
        
        # Message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cari_jawaban_teks))
        application.add_handler(MessageHandler(filters.PHOTO, cari_jawaban_gambar))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        
        # Error handler
        application.add_error_handler(error_handler)
        
        # Jalankan bot
        logger.info("Bot sedang berjalan...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Error in main: {e}")

if __name__ == "__main__":
    main()        
