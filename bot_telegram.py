import os
import re
import pandas as pd
import json
import asyncio
import uuid
from google.cloud import bigquery
from google.cloud import vision
from google.cloud.vision import ImageAnnotatorClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from typing import List, Tuple
import logging
import datetime

# Setup logging dengan format yang lebih detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Konfigurasi dari environment variables
PROJECT_ID = os.getenv("PROJECT_ID")
DATASET_ID = os.getenv("DATASET_ID")
TABLE_ID = os.getenv("TABLE_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", "8443"))
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL", "bottelegram-production-b4c8.up.railway.app")

# Pastikan URL diawali dengan https://
if not RAILWAY_PUBLIC_URL.startswith('http'):
    RAILWAY_PUBLIC_URL = f"https://{RAILWAY_PUBLIC_URL}"

# Validasi environment variables
if not all([PROJECT_ID, DATASET_ID, TABLE_ID, TELEGRAM_TOKEN]):
    logger.error("Satu atau lebih environment variables tidak ditemukan")
    exit(1)

TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# =======================
# 🔑 SETUP BIGQUERY & GOOGLE VISION
# =======================
def initialize_services():
    """Inisialisasi BigQuery dan Google Vision Client"""
    try:
        # Dapatkan service account JSON dari environment variable
        service_account_json = os.getenv("SERVICE_ACCOUNT_JSON")
        
        if service_account_json:
            # Buat file temporary untuk service account
            with open('/tmp/service_account.json', 'w') as f:
                f.write(service_account_json)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = '/tmp/service_account.json'
            logger.info("Menggunakan service account dari environment variable")
        else:
            logger.error("SERVICE_ACCOUNT_JSON tidak ditemukan di environment variables")
            raise ValueError("SERVICE_ACCOUNT_JSON environment variable is required")
        
        # Inisialisasi BigQuery client
        bq_client = bigquery.Client()
       
        # Inisialisasi Vision client
        vision_client = vision.ImageAnnotatorClient()
       
        logger.info("Berhasil menginisialisasi BigQuery dan Vision client")
        return bq_client, vision_client
    except Exception as e:
        logger.error(f"Gagal menginisialisasi services: {e}")
        raise

# Inisialisasi services
bq_client, vision_client = initialize_services()

# =======================
# ⚙️ FUNGSI UTAMA
# =======================
def simpan_soal(soal: str, jawaban: str, source: str = "manual") -> bool:
    """Simpan soal ke BigQuery dengan skema baru"""
    try:
        soal, jawaban = str(soal).strip(), str(jawaban).strip()
        if not soal or not jawaban:
            logger.warning("Soal atau jawaban kosong, tidak disimpan")
            return False
       
        # Normalisasi teks untuk menghindari duplikat karena perbedaan kapitalisasi/spasi
        soal_normalized = re.sub(r'\s+', ' ', soal.lower())
       
        # Cek duplikat di BigQuery
        query = f"""
        SELECT COUNT(*) as count
        FROM `{TABLE_REF}`
        WHERE question_normalized = @question_normalized
        """
       
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("question_normalized", "STRING", soal_normalized)
            ]
        )
       
        query_job = bq_client.query(query, job_config=job_config)
        results = query_job.result()
       
        for row in results:
            if row.count > 0:
                logger.info(f"Soal sudah ada di database: {soal}")
                return False
       
        # Generate ID unik dan timestamp
        unique_id = str(uuid.uuid4())
        timestamp = datetime.datetime.utcnow().isoformat()
       
        # Simpan ke BigQuery dengan skema baru
        rows_to_insert = [{
            "id": unique_id,
            "question": soal,
            "question_normalized": soal_normalized,
            "answer": jawaban,
            "source": source,
            "timestamp": timestamp
        }]
       
        errors = bq_client.insert_rows_json(TABLE_REF, rows_to_insert)
        if not errors:
            logger.info(f"Berhasil menyimpan soal dengan ID {unique_id}: {soal}")
            return True
        else:
            logger.error(f"Error menyimpan soal ke BigQuery: {errors}")
            return False
    except Exception as e:
        logger.error(f"Error menyimpan soal: {e}")
        return False

def parse_qa_text(text: str) -> List[Tuple[str, str]]:
    """Parse teks untuk mengekstrak soal dan jawaban"""
    questions_answers = []
   
    # Beberapa pola yang mungkin untuk mendeteksi soal dan jawaban
    patterns = [
        (r'Soal[:\s]*([^Jawaban]+)Jawaban[:\s]*(.+)', re.IGNORECASE),
        (r'(\d+\.\s*[^?]+\?)\s*Jawab[:\s]*(.+)', re.IGNORECASE),
        (r'([^?]+\?)\s*Jawaban[:\s]*(.+)', re.IGNORECASE),
    ]
   
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
           
        for pattern, flags in patterns:
            match = re.search(pattern, line, flags)
            if match:
                question = match.group(1).strip()
                answer = match.group(2).strip()
                questions_answers.append((question, answer))
                logger.debug(f"Ditemukan pasangan soal-jawaban: {question} -> {answer}")
                break
               
    logger.info(f"Berhasil memparsing {len(questions_answers)} pasangan soal-jawaban dari teks")
    return questions_answers

def ocr_with_google_vision(image_content: bytes) -> str:
    """Melakukan OCR pada gambar menggunakan Google Cloud Vision API"""
    try:
        image = vision.Image(content=image_content)
        response = vision_client.document_text_detection(image=image)
       
        # Ekstrak teks dari hasil OCR
        extracted_text = ""
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    for word in paragraph.words:
                        word_text = "".join([symbol.text for symbol in word.symbols])
                        extracted_text += word_text + " "
                    extracted_text += "\n"
       
        if response.error.message:
            logger.error(f"Google Vision API error: {response.error.message}")
            return ""
           
        logger.info(f"Berhasil mengekstrak teks dari gambar dengan panjang {len(extracted_text)} karakter")
        return extracted_text
    except Exception as e:
        logger.error(f"Error selama proses OCR: {e}")
        return ""

def find_question_answer_columns(headers: List[str]) -> Tuple[List[int], List[int]]:
    """Mencari indeks kolom yang mengandung 'soal' dan 'jawaban' dalam header"""
    question_indices = []
    answer_indices = []
   
    # Normalisasi header untuk pencarian
    normalized_headers = [header.lower().strip() for header in headers]
    logger.debug(f"Header kolom: {headers}")
   
    # Cari kolom yang mengandung kata 'soal'
    for i, header in enumerate(normalized_headers):
        if 'soal' in header or 'pertanyaan' in header:
            question_indices.append(i)
            logger.debug(f"Ditemukan kolom soal di indeks {i}: {headers[i]}")
   
    # Cari kolom yang mengandung kata 'jawaban'
    for i, header in enumerate(normalized_headers):
        if 'jawaban' in header or 'kunci' in header or 'answer' in header:
            answer_indices.append(i)
            logger.debug(f"Ditemukan kolom jawaban di indeks {i}: {headers[i]}")
   
    logger.info(f"Ditemukan {len(question_indices)} kolom soal dan {len(answer_indices)} kolom jawaban")
    return question_indices, answer_indices

def find_answer_from_question(question: str) -> str:
    """Mencari jawaban dari database berdasarkan pertanyaan"""
    try:
        # Normalisasi pertanyaan untuk pencarian
        question_normalized = re.sub(r'\s+', ' ', question.lower())
       
        # Query ke BigQuery untuk mencari jawaban
        query = f"""
        SELECT answer, question_normalized
        FROM `{TABLE_REF}`
        """
       
        query_job = bq_client.query(query)
        results = query_job.result()
       
        best_match = None
        best_score = 0
        question_words = set(question_normalized.split())
       
        for row in results:
            db_question_normalized = row.question_normalized
            db_question_words = set(db_question_normalized.split())
           
            # Hitung kesamaan sederhana berdasarkan kata kunci
            common_words = question_words.intersection(db_question_words)
            similarity = len(common_words) / max(len(question_words), len(db_question_words))
           
            if similarity > best_score:
                best_score = similarity
                best_match = row.answer
       
        if best_match and best_score > 0.3:  # Threshold minimal kemiripan
            logger.info(f"Ditemukan jawaban dengan skor kemiripan {best_score:.2f}: {best_match}")
            return best_match
        else:
            logger.info("Tidak ditemukan jawaban yang cocok")
            return ""
    except Exception as e:
        logger.error(f"Error mencari jawaban: {e}")
        return ""

# =======================
# 🤖 TELEGRAM BOT HANDLER
# =======================
# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /start")
       
        await update.message.reply_text(
            "Halo! Saya adalah Bank Soal Bot.\n\n"
            "Perintah yang tersedia:\n"
            "• /tambah soal=jawaban → tambah soal manual\n"
            "• Upload CSV/Excel/Gambar → import soal otomatis\n"
            "• Kirim pertanyaan (teks/gambar) → saya akan jawab dari bank soal\n"
            "• /ocr → untuk melakukan OCR pada gambar yang dikirim"
        )
    except Exception as e:
        logger.error(f"Error di command /start: {e}")
        await update.message.reply_text("Terjadi kesalahan internal. Silakan coba lagi nanti.")

# /tambah
async def tambah_soal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /tambah"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /tambah dengan args: {context.args}")
       
        if not context.args:
            await update.message.reply_text("Format: /tambah soal=jawaban")
            return
       
        text = " ".join(context.args)
        if "=" not in text:
            await update.message.reply_text("Format salah. Gunakan format: /tambah soal=jawaban")
            return
           
        parts = text.split("=", 1)  # Split hanya pada tanda = pertama
        soal, jawaban = parts[0].strip(), parts[1].strip()
       
        if simpan_soal(soal, jawaban, source="telegram"):
            await update.message.reply_text(f"Soal disimpan: {soal} -> {jawaban}")
        else:
            await update.message.reply_text("Soal sudah ada atau tidak valid.")
    except Exception as e:
        logger.error(f"Error di command /tambah: {e}")
        await update.message.reply_text("Terjadi kesalahan. Pastikan format: /tambah soal=jawaban")

# Cari jawaban dari teks
async def cari_jawaban_teks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari bank soal berdasarkan teks"""
    try:
        user = update.effective_user
        pertanyaan = update.message.text.strip()
        logger.info(f"User {user.username} ({user.id}) mencari jawaban untuk: {pertanyaan}")
       
        if not pertanyaan:
            return
           
        # Cari jawaban dari database
        jawaban = find_answer_from_question(pertanyaan)
       
        if jawaban:
            await update.message.reply_text(f"Jawaban: {jawaban}")
        else:
            await update.message.reply_text("Jawaban tidak ditemukan.")
    except Exception as e:
        logger.error(f"Error di pencarian jawaban teks: {e}")
        await update.message.reply_text("Terjadi kesalahan dalam pencarian.")

# Cari jawaban dari gambar
async def cari_jawaban_gambar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari bank soal berdasarkan gambar"""
    try:
        user = update.effective_user
        photo = update.message.photo[-1]  # Ambil resolusi tertinggi
        file_id = photo.file_id
        file_size = photo.file_size
        logger.info(f"User {user.username} ({user.id}) mencari jawaban dari gambar dengan ID: {file_id} ({file_size} bytes)")
       
        # Download file
        file_obj = await photo.get_file()
        file_bytes = await file_obj.download_as_bytearray()
        logger.info(f"Berhasil mengunduh gambar dengan ukuran {len(file_bytes)} bytes")
       
        # Lakukan OCR untuk mengekstrak pertanyaan
        pertanyaan = ocr_with_google_vision(bytes(file_bytes))
       
        if pertanyaan:
            # Bersihkan teks hasil OCR
            pertanyaan = re.sub(r'\s+', ' ', pertanyaan.strip())
            logger.info(f"Pertanyaan hasil OCR: {pertanyaan}")
           
            # Cari jawaban dari database
            jawaban = find_answer_from_question(pertanyaan)
           
            if jawaban:
                await update.message.reply_text(f"Jawaban: {jawaban}")
            else:
                await update.message.reply_text("Jawaban tidak ditemukan untuk pertanyaan tersebut.")
        else:
            await update.message.reply_text("Gagal mengekstrak pertanyaan dari gambar.")
    except Exception as e:
        logger.error(f"Error di pencarian jawaban gambar: {e}")
        await update.message.reply_text("Terjadi kesalahan dalam memproses gambar.")

# Command untuk OCR
async def ocr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /ocr"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan command /ocr")
       
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text("Silakan balas ke pesan gambar dengan command /ocr")
            return
           
        # Dapatkan file dari pesan yang dibalas
        photo = update.message.reply_to_message.photo[-1]  # Ambil resolusi tertinggi
        file = await photo.get_file()
       
        # Download file
        file_bytes = await file.download_as_bytearray()
        logger.info(f"Berhasil mengunduh gambar dengan ukuran {len(file_bytes)} bytes")
       
        # Lakukan OCR
        extracted_text = ocr_with_google_vision(bytes(file_bytes))
       
        if extracted_text:
            # Parse teks untuk mencari soal dan jawaban
            qa_pairs = parse_qa_text(extracted_text)
            imported = 0
           
            for soal, jawaban in qa_pairs:
                if simpan_soal(soal, jawaban, source="ocr"):
                    imported += 1
           
            if imported > 0:
                await update.message.reply_text(f"OCR selesai, ditemukan dan disimpan {imported} soal.")
            else:
                await update.message.reply_text("OCR selesai, tetapi tidak ditemukan format soal-jawaban yang valid.")
        else:
            await update.message.reply_text("Gagal mengekstrak teks dari gambar.")
    except Exception as e:
        logger.error(f"Error di command /ocr: {e}")
        await update.message.reply_text("Terjadi kesalahan dalam proses OCR.")

# Upload file (CSV/Excel/Gambar)
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk upload file"""
    try:
        user = update.effective_user
        file = update.message.document
        filename = file.file_name
        file_size = file.file_size
        logger.info(f"User {user.username} ({user.id}) mengupload file: {filename} ({file_size} bytes)")
       
        # Buat direktori temp jika belum ada
        os.makedirs("/tmp", exist_ok=True)
        local_path = f"/tmp/{filename}"
       
        # Download file
        file_obj = await file.get_file()
        await file_obj.download_to_drive(local_path)
        logger.info(f"File berhasil diunduh ke {local_path}")
       
        imported = 0
       
        if filename.endswith((".xlsx", ".xls", ".csv")):
            try:
                if filename.endswith((".xlsx", ".xls")):
                    df = pd.read_excel(local_path)
                else:
                    df = pd.read_csv(local_path)
               
                logger.info(f"Berhasil membaca file {filename} dengan {len(df)} baris")
               
                # Dapatkan header kolom
                headers = df.columns.tolist()
               
                # Cari indeks kolom yang mengandung 'soal' dan 'jawaban'
                question_indices, answer_indices = find_question_answer_columns(headers)
               
                if not question_indices or not answer_indices:
                    await update.message.reply_text("Tidak ditemukan kolom dengan header 'soal' atau 'jawaban'.")
                    os.remove(local_path)
                    return
               
                # Proses setiap baris
                for index, row in df.iterrows():
                    # Ambil nilai dari kolom soal dan jawaban
                    questions = [str(row.iloc[i]) for i in question_indices if i < len(row)]
                    answers = [str(row.iloc[i]) for i in answer_indices if i < len(row)]
                   
                    # Simpan pasangan soal-jawaban
                    for soal, jawaban in zip(questions, answers):
                        if simpan_soal(soal, jawaban, source="excel"):
                            imported += 1
               
                logger.info(f"Selesai memproses file, berhasil mengimport {imported} soal")
            except Exception as e:
                logger.error(f"Error memproses file: {e}")
                await update.message.reply_text("Gagal memproses file.")
                return
        elif filename.lower().endswith((".jpg", ".jpeg", ".png")):
            try:
                # Baca file gambar
                with open(local_path, "rb") as image_file:
                    content = image_file.read()
               
                logger.info(f"Berhasil membaca file gambar dengan ukuran {len(content)} bytes")
               
                # Lakukan OCR
                extracted_text = ocr_with_google_vision(content)
               
                if extracted_text:
                    # Parse teks untuk mencari soal dan jawaban
                    qa_pairs = parse_qa_text(extracted_text)
                    for soal, jawaban in qa_pairs:
                        if simpan_soal(soal, jawaban, source="ocr"):
                            imported += 1
                   
                    logger.info(f"Selesai memproses gambar, berhasil mengimport {imported} soal")
                else:
                    await update.message.reply_text("Gagal mengekstrak teks dari gambar.")
                    return
            except Exception as e:
                logger.error(f"Error memproses gambar: {e}")
                await update.message.reply_text("Gagal memproses file gambar.")
                return
        else:
            await update.message.reply_text("Format file tidak didukung. Silakan upload CSV, Excel, atau gambar.")
            os.remove(local_path)
            return
           
        os.remove(local_path)
        await update.message.reply_text(f"Import selesai, ditambahkan {imported} soal.")
       
    except Exception as e:
        logger.error(f"Error di handler file: {e}")
        await update.message.reply_text("Terjadi kesalahan dalam memproses file.")

# Handler untuk gambar yang dikirim langsung (bukan sebagai dokumen)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk gambar yang dikirim langsung"""
    try:
        user = update.effective_user
        photo = update.message.photo[-1]  # Ambil resolusi tertinggi
        file_id = photo.file_id
        file_size = photo.file_size
        logger.info(f"User {user.username} ({user.id}) mengirim gambar dengan ID: {file_id} ({file_size} bytes)")
       
        # Download file
        file_obj = await photo.get_file()
        file_bytes = await file_obj.download_as_bytearray()
        logger.info(f"Berhasil mengunduh gambar dengan ukuran {len(file_bytes)} bytes")
       
        # Lakukan OCR
        extracted_text = ocr_with_google_vision(bytes(file_bytes))
       
        if extracted_text:
            # Parse teks untuk mencari soal dan jawaban
            qa_pairs = parse_qa_text(extracted_text)
            imported = 0
           
            for soal, jawaban in qa_pairs:
                if simpan_soal(soal, jawaban, source="ocr"):
                    imported += 1
           
            logger.info(f"Selesai memproses gambar langsung, berhasil mengimport {imported} soal")
           
            if imported > 0:
                await update.message.reply_text(f"OCR selesai, ditemukan dan disimpan {imported} soal.")
            else:
                await update.message.reply_text("OCR selesai, tetapi tidak ditemukan format soal-jawaban yang valid.")
        else:
            await update.message.reply_text("Gagal mengekstrak teks dari gambar.")
    except Exception as e:
        logger.error(f"Error di handler photo: {e}")
        await update.message.reply_text("Terjadi kesalahan dalam proses OCR.")

# Handler untuk error
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk error"""
    logger.error(f"Exception while handling an update: {context.error}")
   
    # Kirim pesan error ke pengguna jika memungkinkan
    if update and update.message:
        try:
            await update.message.reply_text("Maaf, terjadi kesalahan internal. Silakan coba lagi nanti.")
        except Exception as e:
            logger.error(f"Error mengirim pesan error ke user: {e}")

# =======================
# 🚀 MAIN
# =======================
async def main():
    """Fungsi utama untuk menjalankan bot"""
    try:
        logger.info("Membuat aplikasi bot...")
        app = Application.builder().token(TELEGRAM_TOKEN).build()
       
        # Tambahkan handler
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("tambah", tambah_soal))
        app.add_handler(CommandHandler("ocr", ocr_command))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
       
        # Handler untuk gambar yang dikirim untuk dicari jawabannya
        app.add_handler(MessageHandler(filters.PHOTO & filters.Regex(r'^\s*$'), cari_jawaban_gambar))
       
        # Handler untuk gambar yang dikirim untuk diimpor (mengandung soal dan jawaban)
        app.add_handler(MessageHandler(filters.PHOTO & ~filters.Regex(r'^\s*$'), handle_photo))
       
        # Handler untuk teks pertanyaan
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cari_jawaban_teks))
       
        # Register error handler
        app.add_error_handler(error_handler)
       
        # Setup webhook jika RAILWAY_PUBLIC_URL tersedia
        if RAILWAY_PUBLIC_URL:
            # Pastikan URL tidak memiliki trailing slash
            RAILWAY_PUBLIC_URL = RAILWAY_PUBLIC_URL.rstrip('/')
            webhook_url = f"{RAILWAY_PUBLIC_URL}/webhook/{TELEGRAM_TOKEN}"
            logger.info(f"Setting webhook to: {webhook_url}")
            
            # Hapus webhook yang mungkin sudah ada
            await app.bot.delete_webhook()
            
            # Set webhook baru
            await app.bot.set_webhook(url=webhook_url)
            
            # Jalankan aplikasi dengan webhook
            await app.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=TELEGRAM_TOKEN,
                webhook_url=webhook_url,
                drop_pending_updates=True
            )
        else:
            logger.info("RAILWAY_PUBLIC_URL tidak tersedia, menggunakan polling")
            await app.run_polling()
        
    except Exception as e:
        logger.error(f"Error menjalankan bot: {e}")

if __name__ == "__main__":
    asyncio.run(main())
