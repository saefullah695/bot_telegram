import os
import re
import io
import json
import logging
import datetime
import csv
import uuid
from tempfile import NamedTemporaryFile
from typing import List, Tuple
from collections import Counter
import math
import requests
import unicodedata

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
DATASET_ID = os.getenv("DATASET_ID", "Data")
TABLE_ID = os.getenv("TABLE_ID", "Telegram-new")
TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# OCR.Space API Key
OCR_SPACE_API_KEY = "K84451990188957"

# Global clients
bq_client = None
vision_client = None

# Daftar stopwords (kata umum yang diabaikan)
STOPWORDS = {
    'yang', 'dan', 'di', 'ke', 'dari', 'pada', 'adalah', 'itu', 'dengan', 
    'untuk', 'tidak', 'ini', 'dalam', 'akan', 'juga', 'atau', 'karena',
    'seperti', 'jika', 'saya', 'anda', 'kami', 'mereka', 'ada', 'bisa',
    'dapat', 'lebih', 'sudah', 'belum', 'bisa', 'dapat', 'yaitu', 'yakni',
    'adalah', 'ialah', 'merupakan', 'tersebut', 'tersebutlah'
}

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
        
        # Inisialisasi clients
        bq_client = bigquery.Client(project=PROJECT_ID)
        vision_client = vision.ImageAnnotatorClient()
        logger.info("BigQuery dan Vision clients berhasil diinisialisasi")
        
        # Test koneksi BigQuery
        try:
            test_query = f"SELECT COUNT(*) as count FROM `{TABLE_REF}`"
            query_job = bq_client.query(test_query)
            results = list(query_job.result())
            logger.info(f"Test koneksi BigQuery berhasil. Jumlah data: {results[0].count}")
        except Exception as e:
            logger.error(f"Test koneksi BigQuery gagal: {e}")
            
        return bq_client, vision_client
    except Exception as e:
        logger.error(f"Gagal menginisialisasi services: {e}")
        raise

def normalize_text(text: str) -> str:
    """Normalisasi teks: hapus semua tanda baca dan spasi berlebih"""
    try:
        # Konversi ke bentuk unicode NFKD untuk menangani karakter khusus
        normalized = unicodedata.normalize('NFKD', text)
        
        # Hapus semua tanda baca termasuk karakter khusus seperti ..., :, dll.
        # Pola ini menghapus semua karakter non-alfanumerik kecuali spasi
        normalized = re.sub(r'[^\w\s]', ' ', normalized)
        
        # Hapus spasi berlebih (ganti multiple spasi dengan satu spasi)
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Ubah ke lowercase
        normalized = normalized.lower()
        
        # Hapus spasi di awal dan akhir
        result = normalized.strip()
        
        logger.info(f"Normalisasi teks: '{text}' -> '{result}'")
        return result
    except Exception as e:
        logger.error(f"Error normalisasi teks: {e}")
        return text.lower().strip()

def clean_ocr_text(text: str) -> str:
    """Membersihkan teks hasil OCR dari format tambahan seperti timestamp"""
    try:
        # Hapus timestamp di awal (format HH:MM)
        cleaned = re.sub(r'^\d{1,2}:\d{2}\s*', '', text)
        
        # Hapus karakter khusus yang tidak perlu
        cleaned = re.sub(r'[^\w\s\?\.\,\!\-\:]', ' ', cleaned)
        
        # Hapus spasi berlebih
        cleaned = re.sub(r'\s+', ' ', cleaned)
        
        return cleaned.strip()
    except Exception as e:
        logger.error(f"Error membersihkan teks OCR: {e}")
        return text

def get_keywords(text: str) -> List[str]:
    """Ekstrak kata kunci dari teks"""
    # Normalisasi teks terlebih dahulu
    normalized_text = normalize_text(text)
    words = normalized_text.split()
    keywords = []
    
    for word in words:
        if word not in STOPWORDS and len(word) > 2:
            keywords.append(word)
    
    return keywords

def calculate_similarity(query: str, document: str) -> float:
    """Hitung kemiripan sederhana antara query dan document"""
    # Normalisasi keduanya sebelum perbandingan
    normalized_query = normalize_text(query)
    normalized_document = normalize_text(document)
    
    query_words = set(get_keywords(normalized_query))
    doc_words = set(get_keywords(normalized_document))
    
    if not query_words or not doc_words:
        return 0.0
    
    # Hitung intersection
    intersection = query_words.intersection(doc_words)
    
    # Hitung union
    union = query_words.union(doc_words)
    
    # Jaccard similarity
    return len(intersection) / len(union) if union else 0

def is_except_question(question: str) -> bool:
    """Mendeteksi apakah pertanyaan mengandung kata 'kecuali'"""
    # Normalisasi pertanyaan sebelum pengecekan
    normalized_question = normalize_text(question)
    except_keywords = ['kecuali', 'bukan', 'tidak termasuk', 'kecuali']
    return any(keyword in normalized_question for keyword in except_keywords)

def handle_short_questions(question: str) -> str:
    """Menangani pertanyaan singkat yang umum"""
    # Normalisasi pertanyaan sebelum pencocokan
    normalized_question = normalize_text(question)
    
    # Daftar pertanyaan singkat dan jawabannya
    short_answers = {
        "nama saya adalah": "muhammad alrafka firdaus",
        "siapa nama saya": "muhammad alrafka firdaus",
        "nama saya": "muhammad alrafka firdaus",
        # Tambahkan pertanyaan singkat lainnya di sini
    }
    
    # Cocokkan dengan versi normalisasi
    for key, value in short_answers.items():
        if normalize_text(key) == normalized_question:
            return value
    
    return ""

# =======================
# ⚙️ FUNGSI UTAMA
# =======================

def simpan_soal(question: str, answer: str, source: str = "manual") -> bool:
    """Simpan soal ke BigQuery dengan struktur tabel baru"""
    try:
        question, answer = str(question).strip(), str(answer).strip()
        if not question or not answer:
            logger.warning("Soal atau jawaban kosong, tidak disimpan")
            return False

        # Normalisasi pertanyaan
        question_normalized = normalize_text(question)

        # Cek duplikat berdasarkan question_normalized
        query = f"""
        SELECT COUNT(*) as count 
        FROM `{TABLE_REF}` 
        WHERE question_normalized = @question_normalized
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("question_normalized", "STRING", question_normalized)
            ]
        )
        
        query_job = bq_client.query(query, job_config=job_config)
        result = list(query_job.result())[0]
        
        if result.count > 0:
            logger.info("Soal sudah ada di database, tidak disimpan lagi")
            return False

        # Insert data baru dengan struktur tabel baru
        rows_to_insert = [{
            "id": str(uuid.uuid4()),
            "question": question,
            "question_normalized": question_normalized,
            "answer": answer,
            "source": source,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
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
        matches = re.findall(pattern, text, re.DOTALL)
        
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
        
        raw_text = response.text_annotations[0].text if response.text_annotations else ""
        # Bersihkan teks hasil OCR
        cleaned_text = clean_ocr_text(raw_text)
        logger.info(f"OCR raw: '{raw_text}' -> cleaned: '{cleaned_text}'")
        return cleaned_text
    except Exception as e:
        logger.error(f"Error dalam OCR: {e}")
        return ""

def ocr_with_ocr_space(image_content: bytes) -> str:
    """Melakukan OCR pada gambar menggunakan OCR.Space API sebagai fallback"""
    try:
        # Buat file temporary untuk gambar
        with NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            temp_file.write(image_content)
            temp_file_path = temp_file.name
        
        # Siapkan payload untuk OCR.Space
        payload = {
            'isOverlayRequired': False,
            'apikey': OCR_SPACE_API_KEY,
            'language': 'eng',  # Bisa diganti ke 'ind' untuk Bahasa Indonesia
        }
        
        # Siapkan file untuk diupload
        with open(temp_file_path, 'rb') as f:
            files = {
                'file': (temp_file_path, f, 'image/jpeg')
            }
            
            # Kirim request ke OCR.Space
            response = requests.post(
                'https://api.ocr.space/parse/image',
                files=files,
                data=payload
            )
        
        # Hapus file temporary
        os.unlink(temp_file_path)
        
        # Parse response
        result = response.json()
        
        if result.get('OCRExitCode') == 1:  # Sukses
            parsed_results = result.get('ParsedResults', [])
            if parsed_results:
                raw_text = parsed_results[0].get('ParsedText', '')
                # Bersihkan teks hasil OCR
                cleaned_text = clean_ocr_text(raw_text)
                logger.info(f"OCR.Space raw: '{raw_text}' -> cleaned: '{cleaned_text}'")
                return cleaned_text
        else:
            logger.error(f"OCR.Space error: {result.get('ErrorMessage', 'Unknown error')}")
            return ""
            
        return ""
    except Exception as e:
        logger.error(f"Error dalam OCR.Space: {e}")
        return ""

def find_question_answer_columns(headers: List[str]) -> Tuple[List[int], List[int]]:
    """Mencari indeks kolom yang mengandung 'question' dan 'answer' dalam header"""
    question_indices = []
    answer_indices = []
    
    for i, header in enumerate(headers):
        header_lower = header.lower()
        if any(keyword in header_lower for keyword in ['question', 'soal', 'pertanyaan']):
            question_indices.append(i)
        if any(keyword in header_lower for keyword in ['answer', 'jawaban', 'kunci']):
            answer_indices.append(i)
    
    return question_indices, answer_indices

def find_answer_from_question(question: str) -> str:
    """Mencari jawaban dari database berdasarkan pertanyaan dengan pendekatan bertahap yang diperbaiki"""
    try:
        # Periksa koneksi database
        if bq_client is None:
            logger.error("BigQuery client tidak tersedia")
            return "Maaf, database sedang tidak tersedia. Silakan coba lagi nanti."
        
        # Normalisasi pertanyaan (hapus semua tanda baca dan spasi berlebih)
        question_normalized = normalize_text(question)
        logger.info(f"Pertanyaan setelah normalisasi: '{question_normalized}'")
        
        # Cek dulu apakah pertanyaan termasuk pertanyaan singkat
        short_answer = handle_short_questions(question_normalized)
        if short_answer:
            return short_answer
        
        # Validasi panjang pertanyaan
        if len(question_normalized) < 2:
            logger.warning(f"Pertanyaan terlalu pendek: '{question}' -> '{question_normalized}'")
            return "Pertanyaan terlalu pendek. Silakan berikan pertanyaan yang lebih lengkap."
        
        logger.info(f"Mencari jawaban untuk: '{question}' (normalized: '{question_normalized}')")
        
        # Langkah 1: Cari exact match di question_normalized
        try:
            query = """
            SELECT answer 
            FROM `{0}` 
            WHERE question_normalized = @question_normalized
            LIMIT 1
            """.format(TABLE_REF)
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("question_normalized", "STRING", question_normalized)
                ]
            )
            
            query_job = bq_client.query(query, job_config=job_config)
            results = list(query_job.result())
            
            if results:
                logger.info("Ditemukan exact match di question_normalized")
                return results[0].answer
        except Exception as e:
            logger.error(f"Error pada query exact match question_normalized: {e}")
        
        # Langkah 2: Jika tidak ditemukan, coba dengan normalisasi alternatif
        try:
            # Normalisasi alternatif: hapus semua karakter non-alfanumerik termasuk spasi
            alt_normalized = re.sub(r'[^a-zA-Z0-9]', '', question_normalized)
            logger.info(f"Normalisasi alternatif: '{alt_normalized}'")
            
            query = """
            SELECT answer, question_normalized
            FROM `{0}` 
            WHERE REPLACE(REPLACE(question_normalized, ' ', ''), '_', '') = @alt_normalized
            LIMIT 1
            """.format(TABLE_REF)
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("alt_normalized", "STRING", alt_normalized)
                ]
            )
            
            query_job = bq_client.query(query, job_config=job_config)
            results = list(query_job.result())
            
            if results:
                logger.info("Ditemukan exact match dengan normalisasi alternatif")
                return results[0].answer
        except Exception as e:
            logger.error(f"Error pada query normalisasi alternatif: {e}")
        
        # Langkah 3: Jika masih tidak ditemukan, lakukan fuzzy search dengan LIKE
        try:
            # Ambil kata-kata kunci dari pertanyaan yang sudah dinormalisasi
            words = question_normalized.split()
            if not words:
                raise ValueError("No words found after normalization")
            
            logger.info(f"Kata kunci untuk pencarian fuzzy: {words}")
            
            # Buat kondisi LIKE untuk setiap kata
            conditions = []
            for word in words:
                if len(word) > 2:  # Abaikan kata yang terlalu pendek
                    conditions.append(f"question_normalized LIKE '%{word}%'")
            
            if not conditions:
                raise ValueError("No valid conditions for fuzzy search")
            
            where_clause = " AND ".join(conditions)
            
            query = f"""
            SELECT answer, question_normalized
            FROM `{TABLE_REF}`
            WHERE {where_clause}
            LIMIT 10
            """
            
            query_job = bq_client.query(query)
            results = list(query_job.result())
            
            if results:
                # Hitung kemiripan untuk setiap hasil
                best_match = None
                best_score = 0
                
                for row in results:
                    # Hitung kemiripan
                    score = calculate_similarity(question_normalized, row.question_normalized)
                    
                    if score > best_score:
                        best_score = score
                        best_match = row.answer
                        logger.debug(f"New best match: score={best_score:.3f}, answer={best_match[:50]}...")
                
                # Threshold untuk kemiripan
                if best_match and best_score > 0.5:
                    logger.info(f"Ditemukan jawaban dengan fuzzy search, skor {best_score:.3f}: {best_match}")
                    return best_match
        except Exception as e:
            logger.error(f"Error pada query fuzzy search: {e}")
        
        # Langkah 4: Jika masih tidak ditemukan, balas "Jawaban tidak ditemukan"
        logger.info("Jawaban tidak ditemukan di database")
        return "Jawaban tidak ditemukan"
                
    except Exception as e:
        logger.error(f"Error mencari jawaban: {str(e)}", exc_info=True)
        logger.error(f"Pertanyaan yang dicari: {question}")
        return "Maaf, terjadi kesalahan saat mencari jawaban. Silakan coba lagi nanti."

def process_csv_file(file_bytes: bytes) -> int:
    """Memproses file CSV tanpa menggunakan pandas"""
    try:
        # Decode bytes to string
        content = file_bytes.decode('utf-8')
        csv_reader = csv.reader(io.StringIO(content))
        
        # Read header
        headers = next(csv_reader, [])
        if not headers:
            return 0
            
        # Find question and answer columns
        question_cols, answer_cols = find_question_answer_columns(headers)
        
        if not question_cols or not answer_cols:
            return 0
            
        # Process rows
        count_success = 0
        for row in csv_reader:
            if len(row) > max(question_cols[0], answer_cols[0]):
                question = row[question_cols[0]].strip()
                answer = row[answer_cols[0]].strip()
                
                if question and answer and simpan_soal(question, answer, "csv_upload"):
                    count_success += 1
                    
        return count_success
    except Exception as e:
        logger.error(f"Error processing CSV: {e}")
        return 0

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
            "Halo! Saya adalah bot pencari jawaban dengan akurasi tinggi. Saya dapat membantu Anda:\n\n"
            "1. Mencari jawaban dari pertanyaan teks - langsung ketik pertanyaan Anda\n"
            "2. Mencari jawaban dari gambar - kirim gambar berisi pertanyaan\n"
            "3. Menambah soal dan jawaban ke database - gunakan /tambah\n"
            "4. Memproses file CSV - kirim file tersebut\n\n"
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
            "3. Untuk menambah data, gunakan /tambah atau kirim file CSV\n\n"
            "Bot menggunakan algoritma pencarian bertahap untuk hasil yang akurat!"
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
        
        question, answer = parts[0].strip(), parts[1].strip()
        
        if simpan_soal(question, answer, f"telegram_{user.id}"):
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
        question = update.message.text.strip()
        logger.info(f"User {user.username} ({user.id}) mencari jawaban untuk: '{question}'")
        
        # Validasi panjang pertanyaan
        if len(question) < 2:
            await update.message.reply_text("Pertanyaan terlalu pendek. Silakan berikan pertanyaan yang lebih lengkap.")
            return
        
        # Periksa koneksi database sebelum melanjutkan
        if bq_client is None:
            await update.message.reply_text("Maaf, database sedang tidak tersedia. Silakan coba lagi nanti.")
            return
        
        # Tampilkan status sedang mencari
        await update.message.reply_chat_action(action="typing")
        
        # Cari jawaban
        answer = find_answer_from_question(question)
        
        # Kirim jawaban
        await update.message.reply_text(f"Pertanyaan: {question}\n\nJawaban: {answer}")
        
    except Exception as e:
        logger.error(f"Error mencari jawaban teks: {e}", exc_info=True)
        await update.message.reply_text("Maaf, terjadi kesalahan saat mencari jawaban. Silakan coba lagi nanti.")

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
        
        # Lakukan OCR dengan Google Vision terlebih dahulu
        ocr_text = ocr_with_google_vision(bytes(file_bytes))
        
        # Jika Google Vision gagal, gunakan OCR.Space sebagai fallback
        if not ocr_text:
            logger.info("Google Vision gagal, mencoba OCR.Space sebagai fallback")
            ocr_text = ocr_with_ocr_space(bytes(file_bytes))
            
            # Jika OCR.Space juga gagal
            if not ocr_text:
                await update.message.reply_text("Tidak dapat membaca teks dari gambar. Pastikan gambar jelas dan berisi teks.")
                return
            else:
                logger.info("OCR.Space berhasil sebagai fallback")
        
        # Validasi panjang teks hasil OCR
        if len(ocr_text.strip()) < 2:
            await update.message.reply_text("Teks yang terdeteksi terlalu pendek. Pastikan gambar berisi pertanyaan yang jelas.")
            return
        
        # Cari jawaban berdasarkan teks hasil OCR
        answer = find_answer_from_question(ocr_text)
        
        # Kirim hasil
        await update.message.reply_text(f"Teks terdeteksi: {ocr_text}\n\nJawaban: {answer}")
        
    except Exception as e:
        logger.error(f"Error mencari jawaban gambar: {e}", exc_info=True)
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
        
        # Lakukan OCR dengan Google Vision terlebih dahulu
        ocr_text = ocr_with_google_vision(bytes(file_bytes))
        
        # Jika Google Vision gagal, gunakan OCR.Space sebagai fallback
        if not ocr_text:
            logger.info("Google Vision gagal, mencoba OCR.Space sebagai fallback")
            ocr_text = ocr_with_ocr_space(bytes(file_bytes))
            
            # Jika OCR.Space juga gagal
            if not ocr_text:
                await update.message.reply_text("Tidak dapat membaca teks dari gambar.")
                return
            else:
                logger.info("OCR.Space berhasil sebagai fallback")
        
        await update.message.reply_text(f"Hasil OCR:\n\n{ocr_text}")
        
    except Exception as e:
        logger.error(f"Error di command /ocr: {e}", exc_info=True)
        await update.message.reply_text("Terjadi error saat melakukan OCR. Silakan coba lagi nanti.")

# Upload file (CSV)
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk upload file CSV"""
    try:
        user = update.effective_user
        file = update.message.document
        filename = file.file_name
        file_size = file.file_size
        logger.info(f"User {user.username} ({user.id}) mengupload file: {filename} ({file_size} bytes)")
        
        # Hanya terima file CSV
        if not filename.endswith('.csv'):
            await update.message.reply_text("Hanya file CSV yang didukung.")
            return
        
        # Tampilkan status sedang memproses
        await update.message.reply_chat_action(action="typing")
        
        # Download file
        file_obj = await context.bot.get_file(file.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        # Proses file CSV
        count_success = process_csv_file(file_bytes)
        
        if count_success > 0:
            await update.message.reply_text(f"File berhasil diproses. {count_success} soal ditambahkan ke database.")
        else:
            await update.message.reply_text("Gagal memproses file. Pastikan format file benar dan memiliki kolom 'question' dan 'answer'.")
            
    except Exception as e:
        logger.error(f"Error handling file: {e}", exc_info=True)
        await update.message.reply_text("Terjadi error saat memproses file. Silakan coba lagi nanti.")

# Handler untuk error
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk error"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=True)
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
        logger.error(f"Error in main: {e}", exc_info=True)

if __name__ == "__main__":
    main()
