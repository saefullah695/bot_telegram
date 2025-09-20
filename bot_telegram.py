import os
import re
import io
import json
import logging
import datetime
import csv
import uuid
from tempfile import NamedTemporaryFile
from typing import List, Tuple, Optional, Dict
from collections import Counter
import math
import requests
import unicodedata
from difflib import SequenceMatcher

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
TABLE_ID = os.getenv("TABLE_ID", "mtk")
TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# OCR.Space API Key
OCR_SPACE_API_KEY = "K84451990188957"

# Global clients
bq_client = None
vision_client = None

# Stopwords yang disederhanakan - hanya kata yang benar-benar tidak penting
STOPWORDS = {
    'adalah', 'itu', 'ini', 'tersebut', 'oleh', 'sebuah', 'sebagai',
    'agar', 'supaya', 'bahwa', 'akan', 'sudah', 'telah', 'sedang'
}

# Kata penting yang tidak boleh dihapus (termasuk negasi dan preposisi)
IMPORTANT_WORDS = {
    'tidak', 'bukan', 'kecuali', 'selain', 'hanya', 'cuma', 'melainkan',
    'yang', 'dan', 'di', 'ke', 'dari', 'pada', 'dengan', 'untuk', 
    'dalam', 'juga', 'atau', 'karena', 'seperti', 'jika', 'ya', 'no'
}

# Pola kata tanya untuk deteksi tipe pertanyaan
QUESTION_PATTERNS = {
    'siapa': ['siapa', 'siapakah'],
    'apa': ['apa', 'apakah'],
    'kapan': ['kapan', 'kapankah'],
    'dimana': ['dimana', 'di mana', 'kemana', 'ke mana'],
    'mengapa': ['mengapa', 'kenapa', 'kenapa', 'why'],
    'bagaimana': ['bagaimana', 'gimana', 'how'],
    'berapa': ['berapa', 'berapa banyak', 'berapa jumlah'],
    'manakah': ['yang mana', 'manakah', 'mana'],
    'kecuali': ['kecuali', 'bukan', 'tidak termasuk', 'selain', 'except']
}

# =======================
# SETUP BIGQUERY & GOOGLE VISION
# =======================

def initialize_services():
    """Inisialisasi BigQuery dan Google Vision Client"""
    global bq_client, vision_client
    try:
        service_account_info = os.getenv("SERVICE_ACCOUNT_JSON")
        if service_account_info:
            with NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
                json.dump(json.loads(service_account_info), temp_file)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_file.name
        else:
            logger.warning("SERVICE_ACCOUNT_JSON tidak ditemukan di environment variables")
        
        bq_client = bigquery.Client(project=PROJECT_ID)
        vision_client = vision.ImageAnnotatorClient()
        logger.info("BigQuery dan Vision clients berhasil diinisialisasi")
        
        # Test koneksi BigQuery
        test_query = f"SELECT COUNT(*) as count FROM `{TABLE_REF}` LIMIT 1"
        query_job = bq_client.query(test_query)
        results = list(query_job.result())
        logger.info(f"Test koneksi BigQuery berhasil. Jumlah data: {results[0].count}")
            
        return bq_client, vision_client
    except Exception as e:
        logger.error(f"Gagal menginisialisasi services: {e}")
        raise

def clean_text(text: str) -> str:
    """Pembersihan teks yang lebih hati-hati"""
    try:
        if not text or not text.strip():
            return ""
            
        # Normalisasi unicode
        text = unicodedata.normalize('NFKD', text)
        
        # Hapus karakter control dan non-printable
        text = ''.join(char for char in text if char.isprintable() or char.isspace())
        
        # Standardisasi spasi
        text = re.sub(r'\s+', ' ', text)
        
        # Hapus leading/trailing whitespace
        return text.strip()
    except Exception as e:
        logger.error(f"Error cleaning text: {e}")
        return str(text).strip() if text else ""

def normalize_for_search(text: str) -> str:
    """Normalisasi sesuai dengan format data di database (tanpa tanda baca dan spasi tunggal)"""
    try:
        text = clean_text(text)
        if not text:
            return ""
        
        # Ke lowercase
        text = text.lower()
        
        # Standardisasi kontraksi umum
        replacements = {
            'gimana': 'bagaimana',
            'kenapa': 'mengapa',
            'kapankah': 'kapan',
            'siapakah': 'siapa',
            'apakah': 'apa'
        }
        
        for old, new in replacements.items():
            text = re.sub(rf'\b{old}\b', new, text)
        
        # Hapus SEMUA tanda baca untuk menyesuaikan dengan format database
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Normalisasi spasi menjadi spasi tunggal
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    except Exception as e:
        logger.error(f"Error normalizing text: {e}")
        return clean_text(text).lower()

def extract_keywords(text: str) -> List[str]:
    """Ekstrak kata kunci dengan pendekatan yang lebih baik"""
    try:
        normalized = normalize_for_search(text)
        if not normalized:
            return []
        
        words = normalized.split()
        keywords = []
        
        for word in words:
            word = word.strip('.,!?-')
            
            # Pertahankan kata penting meskipun pendek
            if word in IMPORTANT_WORDS:
                keywords.append(word)
            # Pertahankan kata yang cukup panjang dan bukan stopword
            elif len(word) >= 2 and word not in STOPWORDS:
                keywords.append(word)
        
        return keywords
    except Exception as e:
        logger.error(f"Error extracting keywords: {e}")
        return []

def calculate_text_similarity(text1: str, text2: str) -> float:
    """Hitung similarity untuk teks tanpa tanda baca (sesuai format database)"""
    try:
        # Kedua teks sudah dalam format normalized (tanpa tanda baca)
        if not text1 or not text2:
            return 0.0
        
        # 1. Exact match check dulu
        if text1 == text2:
            return 1.0
        
        # 2. Sequence similarity untuk keseluruhan
        seq_similarity = SequenceMatcher(None, text1, text2).ratio()
        
        # 3. Word-level similarity
        words1 = text1.split()
        words2 = text2.split()
        
        if not words1 or not words2:
            return seq_similarity * 0.3
        
        # Hitung word overlap
        set1, set2 = set(words1), set(words2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        word_similarity = intersection / union if union > 0 else 0.0
        
        # 4. Length similarity (penalti untuk perbedaan panjang yang ekstrem)
        len_ratio = min(len(words1), len(words2)) / max(len(words1), len(words2))
        
        # 5. Important word bonus
        important_matches = (set1 & set2) & {word.replace(',', '').replace('.', '') for word in IMPORTANT_WORDS}
        important_bonus = len(important_matches) * 0.15
        
        # Weighted combination
        final_score = (seq_similarity * 0.2) + (word_similarity * 0.6) + (len_ratio * 0.2) + important_bonus
        
        return min(final_score, 1.0)
    except Exception as e:
        logger.error(f"Error calculating similarity: {e}")
        return 0.0

def detect_question_type(question: str) -> List[str]:
    """Deteksi tipe pertanyaan dengan lebih akurat"""
    normalized = normalize_for_search(question)
    detected_types = []
    
    for q_type, patterns in QUESTION_PATTERNS.items():
        for pattern in patterns:
            if pattern in normalized:
                detected_types.append(q_type)
                break
    
    return detected_types

def clean_ocr_text(text: str) -> str:
    """Pembersihan khusus untuk teks hasil OCR"""
    try:
        if not text:
            return ""
        
        # Hapus timestamp di awal (format HH:MM atau H:MM)
        text = re.sub(r'^\d{1,2}:\d{2}\s*', '', text)
        
        # Hapus prefix pertanyaan yang umum
        text = re.sub(r'^(Q:|Pertanyaan:|Soal:|Question:)\s*', '', text, flags=re.IGNORECASE)
        
        # Perbaiki karakter OCR yang sering salah
        ocr_corrections = {
            r'\b0\b': 'O',  # Angka 0 -> huruf O
            r'\bl\b': 'I',  # huruf l -> huruf I
            r'rn': 'm',     # rn -> m
            r'cl': 'd',     # cl -> d
        }
        
        for pattern, replacement in ocr_corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        return clean_text(text)
    except Exception as e:
        logger.error(f"Error cleaning OCR text: {e}")
        return clean_text(text)

# =======================
# FUNGSI DATABASE
# =======================

def simpan_soal(question: str, answer: str, source: str = "manual") -> bool:
    """Simpan soal ke BigQuery dengan validasi yang lebih baik"""
    try:
        question = clean_text(str(question))
        answer = clean_text(str(answer))
        
        if not question or not answer or len(question) < 3:
            logger.warning(f"Soal tidak valid: question='{question}', answer='{answer}'")
            return False

        question_normalized = normalize_for_search(question)
        
        if not question_normalized:
            logger.warning("Question normalized kosong")
            return False

        # Cek duplikat dengan query yang lebih efisien
        query = """
        SELECT COUNT(*) as count 
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
        result = list(query_job.result())[0]
        
        if result.count > 0:
            logger.info("Soal sudah ada di database")
            return False

        # Insert data baru
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
        
        logger.info(f"Soal berhasil disimpan: {question[:50]}...")
        return True
    except Exception as e:
        logger.error(f"Error menyimpan soal: {e}")
        return False

def find_answer_from_question(question: str) -> str:
    """Pencarian jawaban dengan algoritma yang diperbaiki"""
    try:
        if bq_client is None:
            logger.error("BigQuery client tidak tersedia")
            return "Database tidak tersedia. Silakan coba lagi nanti."
        
        question = clean_text(question)
        if len(question) < 2:
            return "Pertanyaan terlalu pendek. Silakan berikan pertanyaan yang lebih lengkap."
        
        question_normalized = normalize_for_search(question)
        logger.info(f"Mencari jawaban untuk: '{question}' -> normalized: '{question_normalized}'")
        
        # Deteksi tipe pertanyaan
        question_types = detect_question_type(question)
        logger.info(f"Tipe pertanyaan: {question_types}")
        
        # FASE 1: Exact Match
        exact_answer = search_exact_match(question_normalized)
        if exact_answer:
            logger.info("Ditemukan exact match")
            return exact_answer
        
        # FASE 2: Fuzzy Search dengan Similarity
        fuzzy_answer = search_with_similarity(question_normalized, threshold=0.7)
        if fuzzy_answer:
            logger.info("Ditemukan dengan fuzzy search (high threshold)")
            return fuzzy_answer
        
        # FASE 3: Keyword-based Search
        keyword_answer = search_with_keywords(question_normalized, question_types)
        if keyword_answer:
            logger.info("Ditemukan dengan keyword search")
            return keyword_answer
        
        # FASE 4: Lowered threshold fuzzy search
        fuzzy_answer_low = search_with_similarity(question_normalized, threshold=0.5)
        if fuzzy_answer_low:
            logger.info("Ditemukan dengan fuzzy search (low threshold)")
            return fuzzy_answer_low
        
        logger.info("Jawaban tidak ditemukan di database")
        return "Jawaban tidak ditemukan. Coba reformulasi pertanyaan Anda atau periksa ejaan."
                
    except Exception as e:
        logger.error(f"Error mencari jawaban: {e}", exc_info=True)
        return "Terjadi kesalahan saat mencari jawaban. Silakan coba lagi nanti."

def search_exact_match(question_normalized: str) -> Optional[str]:
    """Pencarian exact match"""
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
        
        return results[0].answer if results else None
    except Exception as e:
        logger.error(f"Error dalam exact match search: {e}")
        return None

def search_with_similarity(question_normalized: str, threshold: float = 0.7) -> Optional[str]:
    """Pencarian dengan similarity scoring - optimized untuk database besar"""
    try:
        # Ekstrak kata kunci untuk pre-filtering
        keywords = extract_keywords(question_normalized)
        if not keywords:
            return None
        
        # Ambil kata kunci terpanjang untuk filtering awal
        main_keywords = [kw for kw in keywords if len(kw) >= 3]
        if not main_keywords:
            main_keywords = keywords[:2]  # Fallback ke 2 kata pertama
        
        # Pre-filter dengan kata kunci untuk mengurangi dataset
        conditions = []
        for kw in main_keywords[:3]:  # Maksimal 3 kata kunci utama
            conditions.append(f"question_normalized LIKE '%{kw}%'")
        
        where_clause = " OR ".join(conditions)
        
        query = f"""
        SELECT answer, question_normalized 
        FROM `{TABLE_REF}`
        WHERE {where_clause}
        LIMIT 200
        """
        
        query_job = bq_client.query(query)
        results = list(query_job.result())
        
        if not results:
            return None
        
        best_match = None
        best_score = 0
        
        # Evaluasi similarity untuk kandidat yang sudah difilter
        for row in results:
            score = calculate_text_similarity(question_normalized, row.question_normalized)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = row.answer
                logger.debug(f"New best match: score={best_score:.3f}")
        
        if best_match:
            logger.info(f"Found similarity match with score: {best_score:.3f}")
        
        return best_match
    except Exception as e:
        logger.error(f"Error dalam similarity search: {e}")
        return None

def search_with_keywords(question_normalized: str, question_types: List[str]) -> Optional[str]:
    """Pencarian berdasarkan kata kunci"""
    try:
        keywords = extract_keywords(question_normalized)
        
        if not keywords:
            return None
        
        # Prioritaskan kata kunci yang lebih panjang
        important_keywords = [kw for kw in keywords if len(kw) >= 3]
        if len(important_keywords) < len(keywords):
            important_keywords.extend([kw for kw in keywords if len(kw) == 2])
        
        # Ambil maksimal 5 kata kunci terpenting
        search_keywords = important_keywords[:5]
        
        logger.info(f"Searching dengan keywords: {search_keywords}")
        
        # Buat query dengan REGEXP untuk pencarian yang lebih fleksibel
        keyword_patterns = []
        for kw in search_keywords:
            keyword_patterns.append(f"question_normalized LIKE '%{kw}%'")
        
        # Gunakan OR untuk mendapat lebih banyak hasil
        where_clause = " OR ".join(keyword_patterns)
        
        query = f"""
        SELECT answer, question_normalized,
               (
                   {" + ".join([f"CASE WHEN question_normalized LIKE '%{kw}%' THEN 1 ELSE 0 END" for kw in search_keywords])}
               ) as keyword_matches
        FROM `{TABLE_REF}`
        WHERE {where_clause}
        ORDER BY keyword_matches DESC
        LIMIT 20
        """
        
        query_job = bq_client.query(query)
        results = list(query_job.result())
        
        if not results:
            return None
        
        # Hitung similarity untuk kandidat terbaik
        best_match = None
        best_score = 0
        
        for row in results[:10]:  # Evaluasi top 10 candidates
            score = calculate_text_similarity(question_normalized, row.question_normalized)
            
            # Beri bonus untuk matches dengan keyword lebih banyak
            keyword_bonus = row.keyword_matches * 0.1
            final_score = score + keyword_bonus
            
            if final_score > best_score:
                best_score = final_score
                best_match = row.answer
        
        # Threshold lebih rendah untuk keyword search
        if best_match and best_score >= 0.4:
            logger.info(f"Found keyword match with score: {best_score:.3f}")
            return best_match
        
        return None
    except Exception as e:
        logger.error(f"Error dalam keyword search: {e}")
        return None

# =======================
# OCR FUNCTIONS
# =======================

def ocr_with_google_vision(image_content: bytes) -> str:
    """OCR dengan Google Cloud Vision API"""
    try:
        image = vision.Image(content=image_content)
        response = vision_client.document_text_detection(image=image)
        
        if response.error.message:
            logger.error(f"Error OCR: {response.error.message}")
            return ""
        
        raw_text = response.text_annotations[0].text if response.text_annotations else ""
        cleaned_text = clean_ocr_text(raw_text)
        logger.info(f"Google Vision OCR: '{raw_text[:100]}...' -> '{cleaned_text[:100]}...'")
        return cleaned_text
    except Exception as e:
        logger.error(f"Error dalam Google Vision OCR: {e}")
        return ""

def ocr_with_ocr_space(image_content: bytes) -> str:
    """OCR dengan OCR.Space API sebagai fallback"""
    try:
        with NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            temp_file.write(image_content)
            temp_file_path = temp_file.name
        
        # Perbaikan: gunakan language code yang valid untuk OCR.Space
        payload = {
            'isOverlayRequired': False,
            'apikey': OCR_SPACE_API_KEY,
            'language': 'eng',  # Gunakan 'eng' karena 'ind' tidak didukung
            'OCREngine': 2,     # Engine 2 lebih baik untuk mixed content
            'scale': True,      # Auto-scale image untuk hasil lebih baik
            'isTable': False    # Tidak dalam format tabel
        }
        
        with open(temp_file_path, 'rb') as f:
            files = {'file': (temp_file_path, f, 'image/jpeg')}
            response = requests.post(
                'https://api.ocr.space/parse/image',
                files=files,
                data=payload,
                timeout=30
            )
        
        os.unlink(temp_file_path)
        
        if response.status_code != 200:
            logger.error(f"OCR.Space HTTP error: {response.status_code}")
            return ""
        
        try:
            result = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"OCR.Space JSON decode error: {e}")
            return ""
        
        if result.get('OCRExitCode') == 1:
            parsed_results = result.get('ParsedResults', [])
            if parsed_results:
                raw_text = parsed_results[0].get('ParsedText', '')
                if raw_text:
                    cleaned_text = clean_ocr_text(raw_text)
                    logger.info(f"OCR.Space berhasil: '{raw_text[:50]}...' -> '{cleaned_text[:50]}...'")
                    return cleaned_text
        else:
            error_message = result.get('ErrorMessage', ['Unknown error'])
            if isinstance(error_message, list):
                error_message = ', '.join(error_message)
            logger.error(f"OCR.Space error: {error_message}")
            
        return ""
    except Exception as e:
        logger.error(f"Error dalam OCR.Space: {e}")
        return ""

# =======================
# CSV PROCESSING
# =======================

def find_question_answer_columns(headers: List[str]) -> Tuple[List[int], List[int]]:
    """Cari kolom pertanyaan dan jawaban di CSV"""
    question_indices = []
    answer_indices = []
    
    for i, header in enumerate(headers):
        header_lower = header.lower().strip()
        if any(keyword in header_lower for keyword in ['question', 'soal', 'pertanyaan', 'ask']):
            question_indices.append(i)
        if any(keyword in header_lower for keyword in ['answer', 'jawaban', 'kunci', 'solusi', 'solution']):
            answer_indices.append(i)
    
    return question_indices, answer_indices

def process_csv_file(file_bytes: bytes) -> int:
    """Proses file CSV dengan error handling yang lebih baik"""
    try:
        # Coba UTF-8 dulu
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        content = None
        
        for encoding in encodings:
            try:
                content = file_bytes.decode(encoding)
                logger.info(f"CSV decoded dengan encoding: {encoding}")
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            logger.error("Tidak bisa decode CSV file")
            return 0
        
        csv_reader = csv.reader(io.StringIO(content))
        
        # Baca header
        try:
            headers = next(csv_reader, [])
        except StopIteration:
            logger.error("CSV file kosong")
            return 0
            
        if not headers:
            logger.error("CSV tidak memiliki header")
            return 0
            
        # Cari kolom pertanyaan dan jawaban
        question_cols, answer_cols = find_question_answer_columns(headers)
        
        if not question_cols or not answer_cols:
            logger.error(f"Kolom tidak ditemukan. Headers: {headers}")
            return 0
            
        logger.info(f"Ditemukan kolom - Question: {question_cols[0]}, Answer: {answer_cols[0]}")
        
        # Proses baris data
        count_success = 0
        count_error = 0
        
        for row_num, row in enumerate(csv_reader, start=2):
            try:
                if len(row) > max(question_cols[0], answer_cols[0]):
                    question = clean_text(row[question_cols[0]])
                    answer = clean_text(row[answer_cols[0]])
                    
                    if question and answer and len(question) >= 3:
                        if simpan_soal(question, answer, "csv_upload"):
                            count_success += 1
                        else:
                            count_error += 1
                    else:
                        count_error += 1
                        logger.debug(f"Baris {row_num} tidak valid: Q='{question}', A='{answer}'")
                else:
                    count_error += 1
                    logger.debug(f"Baris {row_num} tidak memiliki kolom yang cukup")
                    
            except Exception as e:
                count_error += 1
                logger.error(f"Error processing row {row_num}: {e}")
                
        logger.info(f"CSV processing complete: {count_success} sukses, {count_error} error")
        return count_success
        
    except Exception as e:
        logger.error(f"Error processing CSV: {e}")
        return 0

def parse_qa_text(text: str) -> List[Tuple[str, str]]:
    """Parse teks untuk mengekstrak Q&A pairs"""
    questions_answers = []
    try:
        # Pattern untuk Q: dan A:
        pattern = r'(?i)(?:Q:|Pertanyaan:|Soal:)\s*(.*?)(?=(?:\n\s*(?:A:|Jawaban:)|\Z))(?:\s*(?:A:|Jawaban:)\s*(.*))?'
        matches = re.findall(pattern, text, re.DOTALL)
        
        for match in matches:
            question = clean_text(match[0])
            answer = clean_text(match[1]) if len(match) > 1 else ""
            
            if question and answer:
                questions_answers.append((question, answer))
        
        # Jika tidak ada pattern, coba split dengan baris baru
        if not questions_answers and "\n" in text:
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            for i in range(0, len(lines)-1, 2):
                if i+1 < len(lines):
                    question = clean_text(lines[i])
                    answer = clean_text(lines[i+1])
                    if question and answer:
                        questions_answers.append((question, answer))
    
    except Exception as e:
        logger.error(f"Error parsing Q&A text: {e}")
    
    return questions_answers

# =======================
# TELEGRAM BOT HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan /start")
        
        welcome_text = (
            "Halo! Saya adalah bot pencari jawaban dengan akurasi tinggi.\n\n"
            "Yang bisa saya lakukan:\n"
            "• Mencari jawaban dari pertanyaan teks\n"
            "• Membaca dan menjawab pertanyaan dari gambar\n"
            "• Menambah soal baru ke database\n"
            "• Memproses file CSV berisi soal-jawab\n\n"
            "Langsung ketik pertanyaan Anda atau gunakan /help untuk info lebih lanjut."
        )
        
        await update.message.reply_text(welcome_text)
    except Exception as e:
        logger.error(f"Error di /start: {e}")
        await update.message.reply_text("Terjadi error. Silakan coba lagi.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /help"""
    try:
        help_text = (
            "BOT PENCARI JAWABAN - PANDUAN PENGGUNAAN\n\n"
            "PERINTAH:\n"
            "/start - Memulai bot\n"
            "/help - Menampilkan bantuan ini\n"
            "/tambah [soal] | [jawaban] - Menambah soal ke database\n"
            "/ocr - OCR pada gambar yang di-reply\n"
            "/debug [pertanyaan] - Debug normalisasi teks\n\n"
            "CARA PENGGUNAAN:\n"
            "1. Ketik langsung pertanyaan untuk mencari jawaban\n"
            "2. Kirim gambar berisi pertanyaan\n"
            "3. Kirim file CSV dengan kolom 'question' dan 'answer'\n\n"
            "CONTOH:\n"
            "- Siapa presiden pertama Indonesia?\n"
            "- /tambah Ibukota Jepang? | Tokyo\n\n"
            "Bot menggunakan AI untuk mencari jawaban yang paling relevan!"
        )
        
        await update.message.reply_text(help_text)
    except Exception as e:
        logger.error(f"Error di /help: {e}")
        await update.message.reply_text("Terjadi error. Silakan coba lagi.")

async def tambah_soal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /tambah"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan /tambah")
        
        if not context.args:
            await update.message.reply_text(
                "Format: /tambah [soal] | [jawaban]\n"
                "Contoh: /tambah Siapa presiden pertama Indonesia? | Soekarno"
            )
            return
        
        full_text = " ".join(context.args)
        if "|" not in full_text:
            await update.message.reply_text(
                "Gunakan | untuk memisahkan soal dan jawaban.\n"
                "Contoh: /tambah Siapa presiden pertama Indonesia? | Soekarno"
            )
            return
        
        parts = full_text.split("|", 1)
        if len(parts) < 2:
            await update.message.reply_text(
                "Format tidak lengkap. Pastikan ada soal dan jawaban.\n"
                "Contoh: /tambah Siapa presiden pertama Indonesia? | Soekarno"
            )
            return
        
        question, answer = parts[0].strip(), parts[1].strip()
        
        if not question or not answer:
            await update.message.reply_text("Soal dan jawaban tidak boleh kosong.")
            return
        
        if simpan_soal(question, answer, f"telegram_{user.id}"):
            await update.message.reply_text(
                f"✅ Soal berhasil ditambahkan!\n\n"
                f"Soal: {question}\n"
                f"Jawaban: {answer}"
            )
        else:
            await update.message.reply_text(
                "❌ Gagal menambahkan soal. Kemungkinan soal sudah ada di database."
            )
            
    except Exception as e:
        logger.error(f"Error di /tambah: {e}")
        await update.message.reply_text("Terjadi error saat menambah soal. Silakan coba lagi.")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /debug - untuk testing normalisasi"""
    try:
        user = update.effective_user
        
        if not context.args:
            await update.message.reply_text(
                "Format: /debug [pertanyaan]\n"
                "Contoh: /debug Siapa presiden pertama Indonesia?"
            )
            return
        
        question = " ".join(context.args)
        normalized = normalize_for_search(question)
        keywords = extract_keywords(normalized)
        
        debug_text = (
            f"🔍 DEBUG NORMALISASI\n\n"
            f"Input: {question}\n"
            f"Normalized: {normalized}\n"
            f"Keywords: {keywords}\n\n"
            f"📊 STATISTIK:\n"
            f"- Panjang asli: {len(question)} karakter\n"
            f"- Panjang normalized: {len(normalized)} karakter\n"
            f"- Jumlah kata: {len(normalized.split())}\n"
            f"- Jumlah keywords: {len(keywords)}"
        )
        
        await update.message.reply_text(debug_text)
        
        # Test pencarian
        if len(normalized) >= 3:
            await update.message.reply_chat_action(action="typing")
            answer = find_answer_from_question(question)
            
            result_text = f"🎯 HASIL PENCARIAN:\n{answer}"
            await update.message.reply_text(result_text)
        
    except Exception as e:
        logger.error(f"Error di /debug: {e}")
        await update.message.reply_text("Terjadi error saat debugging.")

async def cari_jawaban_teks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari teks"""
    try:
        user = update.effective_user
        question = update.message.text.strip()
        logger.info(f"User {user.username} ({user.id}) bertanya: '{question}'")
        
        if len(question) < 2:
            await update.message.reply_text(
                "Pertanyaan terlalu pendek. Silakan berikan pertanyaan yang lebih lengkap."
            )
            return
        
        if bq_client is None:
            await update.message.reply_text(
                "Database sedang tidak tersedia. Silakan coba lagi nanti."
            )
            return
        
        # Show typing indicator
        await update.message.reply_chat_action(action="typing")
        
        # Cari jawaban
        answer = find_answer_from_question(question)
        
        # Format response
        if answer and answer != "Jawaban tidak ditemukan":
            response = f"❓ Pertanyaan: {question}\n\n✅ Jawaban: {answer}"
        else:
            response = f"❓ Pertanyaan: {question}\n\n❌ {answer}"
            
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error mencari jawaban teks: {e}", exc_info=True)
        await update.message.reply_text(
            "Terjadi kesalahan saat mencari jawaban. Silakan coba lagi nanti."
        )

async def cari_jawaban_gambar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mencari jawaban dari gambar"""
    try:
        user = update.effective_user
        photo = update.message.photo[-1]
        logger.info(f"User {user.username} ({user.id}) kirim gambar: {photo.file_id}")
        
        await update.message.reply_chat_action(action="typing")
        
        # Download gambar
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        
        # OCR dengan Google Vision dulu
        ocr_text = ocr_with_google_vision(bytes(file_bytes))
        
        # Fallback ke OCR.Space jika gagal
        if not ocr_text or len(ocr_text.strip()) < 3:
            logger.info("Google Vision gagal, mencoba OCR.Space")
            ocr_text = ocr_with_ocr_space(bytes(file_bytes))
            
            if not ocr_text or len(ocr_text.strip()) < 3:
                await update.message.reply_text(
                    "❌ Tidak dapat membaca teks dari gambar.\n"
                    "Pastikan gambar jelas dan berisi teks yang dapat dibaca."
                )
                return
        
        logger.info(f"OCR hasil: '{ocr_text}'")
        
        # Cari jawaban berdasarkan teks OCR
        answer = find_answer_from_question(ocr_text)
        
        # Format response
        response = f"📷 Teks terdeteksi: {ocr_text}\n\n"
        
        if answer and answer != "Jawaban tidak ditemukan":
            response += f"✅ Jawaban: {answer}"
        else:
            response += f"❌ {answer}"
            
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error mencari jawaban gambar: {e}", exc_info=True)
        await update.message.reply_text(
            "Terjadi error saat memproses gambar. Silakan coba lagi nanti."
        )

async def ocr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /ocr"""
    try:
        user = update.effective_user
        logger.info(f"User {user.username} ({user.id}) menggunakan /ocr")
        
        # Cek apakah ada gambar yang di-reply
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text(
                "Kirim gambar terlebih dahulu, lalu reply dengan /ocr"
            )
            return
        
        await update.message.reply_chat_action(action="typing")
        
        # Dapatkan gambar dari pesan yang di-reply
        photo = update.message.reply_to_message.photo[-1]
        
        # Download gambar
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        
        # OCR dengan Google Vision dulu
        ocr_text = ocr_with_google_vision(bytes(file_bytes))
        
        # Fallback ke OCR.Space jika gagal
        if not ocr_text:
            logger.info("Google Vision gagal, mencoba OCR.Space")
            ocr_text = ocr_with_ocr_space(bytes(file_bytes))
            
            if not ocr_text:
                await update.message.reply_text("❌ Tidak dapat membaca teks dari gambar.")
                return
        
        await update.message.reply_text(f"📄 Hasil OCR:\n\n{ocr_text}")
        
    except Exception as e:
        logger.error(f"Error di /ocr: {e}", exc_info=True)
        await update.message.reply_text("Terjadi error saat melakukan OCR. Silakan coba lagi.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk upload file CSV"""
    try:
        user = update.effective_user
        file = update.message.document
        filename = file.file_name
        file_size = file.file_size
        logger.info(f"User {user.username} ({user.id}) upload: {filename} ({file_size} bytes)")
        
        # Validasi file CSV
        if not filename or not filename.lower().endswith('.csv'):
            await update.message.reply_text(
                "❌ Hanya file CSV yang didukung.\n"
                "Pastikan file memiliki ekstensi .csv"
            )
            return
        
        # Validasi ukuran file (maksimal 10MB)
        if file_size > 10 * 1024 * 1024:
            await update.message.reply_text(
                "❌ File terlalu besar. Maksimal 10MB."
            )
            return
        
        await update.message.reply_chat_action(action="typing")
        await update.message.reply_text("⏳ Memproses file CSV...")
        
        # Download file
        file_obj = await context.bot.get_file(file.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        # Proses file CSV
        count_success = process_csv_file(file_bytes)
        
        if count_success > 0:
            await update.message.reply_text(
                f"✅ File berhasil diproses!\n"
                f"📊 {count_success} soal ditambahkan ke database."
            )
        else:
            await update.message.reply_text(
                "❌ Gagal memproses file.\n\n"
                "Pastikan:\n"
                "• File berformat CSV\n"
                "• Ada kolom 'question' dan 'answer'\n"
                "• Data tidak kosong"
            )
            
    except Exception as e:
        logger.error(f"Error handling file: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Terjadi error saat memproses file. Silakan coba lagi nanti."
        )

async def handle_text_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk file teks berisi Q&A"""
    try:
        user = update.effective_user
        message = update.message
        
        # Cek apakah ada file teks
        if not message.document:
            return
            
        file = message.document
        filename = file.file_name
        
        # Hanya proses file teks
        if not filename or not filename.lower().endswith(('.txt', '.text')):
            return
        
        logger.info(f"User {user.username} ({user.id}) upload file teks: {filename}")
        
        await message.reply_chat_action(action="typing")
        await message.reply_text("⏳ Memproses file teks...")
        
        # Download file
        file_obj = await context.bot.get_file(file.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        # Decode file
        try:
            content = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            content = file_bytes.decode('latin-1')
        
        # Parse Q&A pairs
        qa_pairs = parse_qa_text(content)
        
        if not qa_pairs:
            await message.reply_text(
                "❌ Tidak ditemukan format Q&A yang valid.\n\n"
                "Format yang didukung:\n"
                "Q: Pertanyaan?\n"
                "A: Jawaban\n\n"
                "atau:\n\n"
                "Pertanyaan?\n"
                "Jawaban"
            )
            return
        
        # Simpan ke database
        count_success = 0
        for question, answer in qa_pairs:
            if simpan_soal(question, answer, f"text_file_{user.id}"):
                count_success += 1
        
        await message.reply_text(
            f"✅ File teks berhasil diproses!\n"
            f"📊 {count_success} dari {len(qa_pairs)} soal ditambahkan ke database."
        )
        
    except Exception as e:
        logger.error(f"Error handling text file: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Terjadi error saat memproses file teks."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler"""
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    
    if update and update.message:
        try:
            await update.message.reply_text(
                "❌ Terjadi error tidak terduga. Silakan coba lagi atau hubungi admin."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

# =======================
# MAIN FUNCTION
# =======================

def main():
    """Fungsi utama untuk menjalankan bot"""
    try:
        # Validasi environment variables
        TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        if not TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables")
            return
        
        # Inisialisasi services
        logger.info("Menginisialisasi services...")
        initialize_services()
        
        # Buat application
        application = Application.builder().token(TOKEN).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("tambah", tambah_soal))
        application.add_handler(CommandHandler("ocr", ocr_command))
        application.add_handler(CommandHandler("debug", debug_command))  # Tambah debug handler
        
        # Message handlers - urutan penting!
        application.add_handler(MessageHandler(
            filters.Document.FileExtension("csv"), 
            handle_file
        ))
        application.add_handler(MessageHandler(
            filters.Document.FileExtension("txt") | filters.Document.FileExtension("text"), 
            handle_text_file
        ))
        application.add_handler(MessageHandler(filters.PHOTO, cari_jawaban_gambar))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            cari_jawaban_teks
        ))
        
        # Error handler
        application.add_error_handler(error_handler)
        
        # Jalankan bot
        logger.info("🤖 Bot sedang berjalan...")
        logger.info("Tekan Ctrl+C untuk menghentikan bot")
        
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        logger.info("Bot dihentikan oleh user")
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
    finally:
        logger.info("Bot shutdown complete")

if __name__ == "__main__":
    main()

