import os
import re
import io
import json
import logging
import datetime
import csv
import uuid
import requests
from tempfile import NamedTemporaryFile
from typing import List, Tuple
from collections import Counter
import math

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

# Konfigurasi OCR
OCR_API_KEY = "K84451990188957"
OCR_API_URL = "https://api.ocr.space/parse/image"

# Global clients
bq_client = None
vision_client = None

# Daftar stopwords untuk bahasa Indonesia
STOPWORDS_ID = {
    'yang', 'dan', 'di', 'ke', 'dari', 'pada', 'adalah', 'itu', 'dengan', 
    'untuk', 'tidak', 'ini', 'dalam', 'akan', 'juga', 'atau', 'karena',
    'seperti', 'jika', 'saya', 'anda', 'kami', 'mereka', 'ada', 'bisa',
    'dapat', 'lebih', 'sudah', 'belum', 'bisa', 'dapat', 'yaitu', 'yakni',
    'adalah', 'ialah', 'merupakan', 'tersebut', 'tersebutlah', 'oleh',
    'sebuah', 'para', 'bagi', 'antar', 'dalam', 'terhadap', 'sampai',
    'setelah', 'sebelum', 'sejak', 'selama', 'tentang', 'agar', 'supaya',
    'hingga', 'sampai', 'sedangkan', 'melainkan', 'tetapi', 'namun'
}

# Daftar stopwords untuk bahasa Inggris
STOPWORDS_EN = {
    'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i',
    'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at',
    'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her',
    'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there',
    'their', 'what', 'so', 'up', 'out', 'if', 'about', 'who', 'get',
    'which', 'go', 'me', 'when', 'make', 'can', 'like', 'time', 'no',
    'just', 'him', 'know', 'take', 'people', 'into', 'year', 'your',
    'good', 'some', 'could', 'them', 'see', 'other', 'than', 'then',
    'now', 'look', 'only', 'come', 'its', 'over', 'think', 'also',
    'back', 'after', 'use', 'two', 'how', 'our', 'work', 'first',
    'well', 'way', 'even', 'new', 'want', 'because', 'any', 'these',
    'give', 'day', 'most', 'us'
}

# Daftar stopwords gabungan
STOPWORDS = STOPWORDS_ID.union(STOPWORDS_EN)

# Daftar sinonim untuk bahasa Indonesia
SINONIM_ID = {
    'prosedur': ['proses', 'cara', 'langkah', 'metode'],
    'personil': ['pegawai', 'karyawan', 'staf', 'tenaga kerja'],
    'toko': ['outlet', 'gerai', 'cabang', 'toko'],
    'barang': ['produk', 'item', 'material', 'benda'],
    'rusak': ['cacat', 'defect', 'error', 'salah'],
    'pengiriman': ['kirim', 'antar', 'delivery', 'pengantaran'],
    'supplier': ['pemasok', 'vendor', 'penyedia', 'pemasok'],
    'stock': ['stok', 'persediaan', 'inventory', 'cadangan'],
    'opname': ['cek', 'hitung', 'audit', 'pemeriksaan'],
    'konsumen': ['pelanggan', 'customer', 'pembeli', 'konsumen'],
    'admin': ['operator', 'pengelola', 'manager', 'administrator'],
    'area': ['wilayah', 'daerah', 'region', 'area'],
    'manager': ['pemimpin', 'kepala', 'supervisor', 'manajer'],
    'coordinator': ['koordinator', 'pengatur', 'penyelenggara', 'koordinator'],
    'hitung': ['kalkulasi', 'perhitungan', 'menghitung'],
    'lakukan': ['kerjakan', 'laksanakan', 'eksekusi'],
    'dapatkan': ['peroleh', 'canai', 'dapatkan'],
    'gunakan': 'pakai',
    'cari': ['temukan', 'gali', 'telusuri'],
    'buat': ['ciptakan', 'hasilkan', 'lakukan'],
    'tambah': ['tambahkan', 'plus', 'lebih'],
    'kurang': ['kurangi', 'minus', 'sedikit'],
    'ubah': ['modifikasi', 'rubah', 'ganti'],
    'hasil': ['output', 'result', 'outcome'],
    'masalah': ['problem', 'isu', 'kendala'],
    'solusi': ['penyelesaian', 'jawaban', 'solution'],
    'data': ['informasi', 'rekaman', 'catatan'],
    'sistem': ['system', 'struktur', 'kerangka']
}

# Daftar sinonim untuk bahasa Inggris
SINONIM_EN = {
    'procedure': ['process', 'method', 'steps', 'way'],
    'personnel': ['staff', 'employee', 'worker', 'team'],
    'store': ['shop', 'outlet', 'retail', 'market'],
    'goods': ['products', 'items', 'merchandise', 'commodities'],
    'damaged': ['broken', 'defective', 'faulty', 'impaired'],
    'delivery': ['shipping', 'transport', 'distribution', 'dispatch'],
    'supplier': ['vendor', 'provider', 'distributor', 'source'],
    'stock': ['inventory', 'supply', 'reserve', 'accumulation'],
    'count': ['calculate', 'tally', 'compute', 'reckon'],
    'consumer': ['customer', 'client', 'buyer', 'purchaser'],
    'admin': ['administrator', 'manager', 'operator', 'supervisor'],
    'area': ['region', 'zone', 'district', 'territory'],
    'manager': ['supervisor', 'director', 'executive', 'head'],
    'coordinator': ['organizer', 'arranger', 'planner', 'facilitator'],
    'find': ['search', 'look for', 'seek', 'discover'],
    'make': ['create', 'produce', 'generate', 'build'],
    'add': ['plus', 'include', 'append', 'attach'],
    'remove': ['subtract', 'delete', 'eliminate', 'take away'],
    'change': ['modify', 'alter', 'adjust', 'transform'],
    'result': ['outcome', 'output', 'consequence', 'effect'],
    'problem': ['issue', 'trouble', 'difficulty', 'challenge'],
    'solution': ['answer', 'resolution', 'fix', 'remedy'],
    'data': ['information', 'facts', 'details', 'records'],
    'system': ['structure', 'framework', 'organization', 'arrangement']
}

# Daftar sinonim gabungan
SINONIM = {**SINONIM_ID, **SINONIM_EN}

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
        
        # Inisialisasi BigQuery client
        bq_client = bigquery.Client(project=PROJECT_ID)
        
        # Inisialisasi Vision client (jika service account tersedia)
        try:
            vision_client = vision.ImageAnnotatorClient()
            logger.info("Google Vision client berhasil diinisialisasi")
        except Exception as e:
            logger.warning(f"Gagal menginisialisasi Google Vision client: {e}")
            vision_client = None
        
        logger.info("BigQuery client berhasil diinisialisasi")
        
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

def detect_language(text: str) -> str:
    """Deteksi bahasa dari teks (sederhana)"""
    # Cek karakter khas bahasa Indonesia
    indonesian_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
    text_chars = set(text.lower())
    
    # Hitung persentase karakter non-alfabet
    non_alpha = sum(1 for c in text.lower() if c not in indonesian_chars)
    non_alpha_ratio = non_alpha / len(text) if text else 0
    
    # Jika banyak karakter non-alfabet, kemungkinan bahasa Indonesia
    if non_alpha_ratio > 0.1:
        return 'id'
    
    # Cek kata-kata khas bahasa Indonesia
    id_keywords = ['yang', 'dan', 'di', 'ke', 'dari', 'pada', 'adalah', 'untuk', 
                   'tidak', 'dengan', 'ini', 'dalam', 'akan', 'juga', 'atau', 
                   'karena', 'seperti', 'jika', 'saya', 'anda', 'kami', 'mereka']
    
    words = text.lower().split()
    id_count = sum(1 for word in words if word in id_keywords)
    
    # Jika lebih dari 10% kata adalah kata khas Indonesia
    if id_count / len(words) > 0.1 if words else False:
        return 'id'
    
    # Default ke bahasa Inggris
    return 'en'

def normalize_question(question: str) -> str:
    """Normalisasi pertanyaan untuk pencarian"""
    try:
        # Hapus karakter khusus, ubah ke lowercase, dan hapus spasi berlebih
        normalized = re.sub(r'[^\w\s]', '', question.lower())  # Hapus karakter khusus
        normalized = re.sub(r'\s+', ' ', normalized)  # Hapus spasi berlebih
        result = normalized.strip()
        logger.info(f"Normalisasi pertanyaan: '{question}' -> '{result}'")
        return result
    except Exception as e:
        logger.error(f"Error normalisasi pertanyaan: {e}")
        return question.lower().strip()

def get_keywords(text: str) -> List[str]:
    """Ekstrak kata kunci dari teks"""
    words = text.split()
    keywords = []
    
    for word in words:
        if word not in STOPWORDS and len(word) > 2:
            keywords.append(word)
    
    return keywords

def calculate_similarity(query: str, document: str) -> float:
    """Hitung kemiripan sederhana antara query dan document"""
    query_words = set(get_keywords(query))
    doc_words = set(get_keywords(document))
    
    if not query_words or not doc_words:
        return 0.0
    
    # Hitung intersection
    intersection = query_words.intersection(doc_words)
    
    # Hitung union
    union = query_words.union(doc_words)
    
    # Jaccard similarity
    return len(intersection) / len(union) if union else 0

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
        question_normalized = normalize_question(question)

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
        pattern = r'(?i)(Q:|Pertanyaan:|Soal:|Question:)\s*(.*?)(?=(?:A:|Jawaban:|Answer:|$))(?:\s*(?:A:|Jawaban:|Answer:)\s*(.*))?'
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

def ocr_with_ocr_space(image_content: bytes, language: str = 'eng') -> str:
    """Melakukan OCR pada gambar menggunakan OCR.Space API"""
    try:
        # Prepare the request
        files = {'file': ('image.jpg', image_content, 'image/jpeg')}
        data = {
            'apikey': OCR_API_KEY,
            'language': language,
            'isOverlayRequired': 'false',
            'detectOrientation': 'true',
            'scale': 'true'
        }
        
        # Send request to OCR.Space
        response = requests.post(OCR_API_URL, files=files, data=data)
        
        if response.status_code != 200:
            logger.error(f"OCR.Space API error: {response.status_code} - {response.text}")
            return ""
        
        result = response.json()
        
        if result.get('OCRExitCode') != 1:
            logger.error(f"OCR.Space processing error: {result.get('ErrorMessage', 'Unknown error')}")
            return ""
        
        # Extract text from the result
        parsed_results = result.get('ParsedResults', [])
        if not parsed_results:
            logger.warning("No parsed results from OCR.Space")
            return ""
        
        extracted_text = ""
        for parsed_result in parsed_results:
            text_overlay = parsed_result.get('TextOverlay', {})
            lines = text_overlay.get('Lines', [])
            
            for line in lines:
                words = line.get('Words', [])
                line_text = " ".join([word.get('WordText', '') for word in words])
                extracted_text += line_text + "\n"
        
        logger.info(f"Berhasil mengekstrak teks dari gambar dengan OCR.Space ({language}), panjang: {len(extracted_text)} karakter")
        return extracted_text.strip()
    except Exception as e:
        logger.error(f"Error dalam OCR.Space: {e}")
        return ""

def ocr_with_google_vision(image_content: bytes, language: str = 'en') -> str:
    """Melakukan OCR pada gambar menggunakan Google Cloud Vision API (backup)"""
    try:
        if vision_client is None:
            logger.warning("Google Vision client tidak tersedia")
            return ""
        
        image = vision.Image(content=image_content)
        
        # Set language hints
        context = vision.ImageContext(
            language_hints=[language]
        )
        
        response = vision_client.document_text_detection(image=image, image_context=context)
        
        if response.error.message:
            logger.error(f"Google Vision API error: {response.error.message}")
            return ""
        
        extracted_text = response.text_annotations[0].text if response.text_annotations else ""
        logger.info(f"Berhasil mengekstrak teks dari gambar dengan Google Vision ({language}), panjang: {len(extracted_text)} karakter")
        return extracted_text
    except Exception as e:
        logger.error(f"Error dalam Google Vision: {e}")
        return ""

def ocr_with_fallback(image_content: bytes, text_language: str = None) -> str:
    """Melakukan OCR dengan fallback mechanism"""
    # Deteksi bahasa jika tidak ditentukan
    if text_language is None:
        text_language = 'eng'  # Default ke Inggris
    
    # Konversi kode bahasa
    ocr_language = 'eng' if text_language == 'en' else 'ind'
    vision_language = 'en' if text_language == 'en' else 'id'
    
    # Coba OCR.Space terlebih dahulu
    logger.info(f"Mencoba OCR dengan OCR.Space (bahasa: {ocr_language})...")
    ocr_text = ocr_with_ocr_space(image_content, ocr_language)
    
    if ocr_text:
        logger.info("OCR.Space berhasil, mengembalikan hasil")
        return ocr_text
    
    # Jika OCR.Space gagal, coba Google Vision
    logger.info(f"OCR.Space gagal, mencoba fallback ke Google Vision (bahasa: {vision_language})...")
    ocr_text = ocr_with_google_vision(image_content, vision_language)
    
    if ocr_text:
        logger.info("Google Vision berhasil sebagai fallback")
        return ocr_text
    
    # Jika keduanya gagal
    logger.error("Kedua metode OCR gagal")
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
    """Mencari jawaban dari database berdasarkan pertanyaan dengan pendekatan bertahap"""
    try:
        # Periksa koneksi database
        if bq_client is None:
            logger.error("BigQuery client tidak tersedia")
            return "Maaf, database sedang tidak tersedia. Silakan coba lagi nanti."
        
        # Normalisasi pertanyaan untuk pencarian
        question_normalized = normalize_question(question)
        
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
        
        # Langkah 2: Jika tidak ditemukan, cari exact match di question
        try:
            query = """
            SELECT answer 
            FROM `{0}` 
            WHERE question = @question
            LIMIT 1
            """.format(TABLE_REF)
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("question", "STRING", question)
                ]
            )
            
            query_job = bq_client.query(query, job_config=job_config)
            results = list(query_job.result())
            
            if results:
                logger.info("Ditemukan exact match di question")
                return results[0].answer
        except Exception as e:
            logger.error(f"Error pada query exact match question: {e}")
        
        # Langkah 3: Jika masih tidak ditemukan, cari dengan kemiripan kata kunci
        try:
            # Ekstrak kata kunci dari pertanyaan
            keywords = get_keywords(question_normalized)
            
            if not keywords:
                logger.warning("Tidak ada kata kunci yang ditemukan")
                return "Jawaban tidak ditemukan di database. Coba gunakan kata kunci yang lebih spesifik."
            
            # Ambil 3 kata kunci terpanjang untuk filter
            keywords.sort(key=len, reverse=True)
            top_keywords = keywords[:3]
            
            # Buat query untuk mencari data yang mengandung kata kunci
            conditions = []
            for keyword in top_keywords:
                conditions.append(f"question_normalized LIKE '%{keyword}%'")
                conditions.append(f"question LIKE '%{keyword}%'")
            
            where_clause = " OR ".join(conditions)
            
            query = f"""
            SELECT answer, question_normalized, question
            FROM `{TABLE_REF}`
            WHERE {where_clause}
            LIMIT 100
            """
            
            query_job = bq_client.query(query)
            results = list(query_job.result())
            
            if not results:
                logger.info("Tidak ditemukan data yang mengandung kata kunci")
                return "Jawaban tidak ditemukan di database. Coba gunakan kata kunci yang lebih spesifik."
            
            # Hitung kemiripan untuk setiap hasil
            best_match = None
            best_score = 0
            
            for row in results:
                # Hitung kemiripan dengan question_normalized
                score_normalized = calculate_similarity(question_normalized, row.question_normalized)
                
                # Hitung kemiripan dengan question asli
                score_original = calculate_similarity(question_normalized, row.question)
                
                # Ambil skor tertinggi
                score = max(score_normalized, score_original)
                
                if score > best_score:
                    best_score = score
                    best_match = row.answer
                    logger.debug(f"New best match: score={best_score:.3f}, answer={best_match[:50]}...")
            
            # Threshold untuk kemiripan
            if best_match and best_score > 0.3:
                logger.info(f"Ditemukan jawaban dengan skor kemiripan {best_score:.3f}: {best_match}")
                return best_match
            else:
                logger.info(f"Tidak ditemukan jawaban yang cukup mirip (best score: {best_score:.3f})")
                return "Jawaban tidak ditemukan di database. Coba gunakan kata kunci yang lebih spesifik."
                
        except Exception as e:
            logger.error(f"Error pada query kemiripan: {e}")
            return "Maaf, terjadi kesalahan saat mencari jawaban. Silakan coba lagi nanti."
            
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
            "🌟 Halo! Saya adalah bot pencari jawaban multibahasa. Saya dapat membantu Anda:\n\n"
            "📝 1. Mencari jawaban dari pertanyaan teks - langsung ketik pertanyaan Anda\n"
            "🖼️ 2. Mencari jawaban dari gambar - kirim gambar berisi pertanyaan\n"
            "➕ 3. Menambah soal dan jawaban ke database - gunakan /tambah\n"
            "📊 4. Memproses file CSV - kirim file tersebut\n\n"
            "🌍 Saya mendukung bahasa Indonesia dan Inggris!\n\n"
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
            "🔹 Perintah yang tersedia:\n"
            "   /start - Memulai bot\n"
            "   /help - Menampilkan bantuan ini\n"
            "   /tambah [soal] | [jawaban] - Menambah soal dan jawaban ke database\n"
            "   /ocr - Melakukan OCR pada gambar yang dikirim sebelumnya\n\n"
            "🔹 Cara penggunaan:\n"
            "   1. Untuk mencari jawaban, ketik langsung pertanyaan Anda\n"
            "   2. Untuk mencari jawaban dari gambar, kirim gambar berisi pertanyaan\n"
            "   3. Untuk menambah data, gunakan /tambah atau kirim file CSV\n\n"
            "🌍 Dukungan Bahasa:\n"
            "   • Indonesia (Bahasa Indonesia)\n"
            "   • English (Bahasa Inggris)\n\n"
            "🤖 Bot menggunakan OCR.Space dan Google Vision sebagai backup!"
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
            await update.message.reply_text("Soal dan jawaban berhasil ditambahkan! ✅")
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
        
        # Deteksi bahasa dari caption jika ada
        text_language = None
        if update.message.caption:
            text_language = detect_language(update.message.caption)
        
        # Lakukan OCR dengan fallback mechanism
        ocr_text = ocr_with_fallback(bytes(file_bytes), text_language)
        
        if not ocr_text:
            await update.message.reply_text("Tidak dapat membaca teks dari gambar. Pastikan gambar jelas dan berisi teks.")
            return
        
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
        
        # Deteksi bahasa dari pesan yang dibalas
        text_language = None
        if update.message.reply_to_message and update.message.reply_to_message.caption:
            text_language = detect_language(update.message.reply_to_message.caption)
        
        # Lakukan OCR dengan fallback mechanism
        ocr_text = ocr_with_fallback(bytes(file_bytes), text_language)
        
        if not ocr_text:
            await update.message.reply_text("Tidak dapat membaca teks dari gambar.")
            return
        
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
            await update.message.reply_text(f"File berhasil diproses. {count_success} soal ditambahkan ke database. ✅")
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
