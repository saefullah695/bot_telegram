def normalize_question(question: str) -> str:
    """Normalisasi pertanyaan untuk pencarian dengan menghapus semua tanda baca dan spasi berlebih"""
    try:
        # Ubah ke lowercase
        normalized = question.lower()
        
        # Hapus semua tanda baca (termasuk karakter khusus)
        # Pola regex ini menghapus semua karakter non-alphanumeric dan non-spasi
        normalized = re.sub(r'[^\w\s]', ' ', normalized)
        
        # Hapus spasi berlebih (ganti multiple spasi dengan single spasi)
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Hapus spasi di awal dan akhir string
        result = normalized.strip()
        
        # Log hasil normalisasi untuk debugging
        logger.info(f"Normalisasi pertanyaan: '{question}' -> '{result}'")
        
        return result
    except Exception as e:
        logger.error(f"Error normalisasi pertanyaan: {e}")
        # Fallback: sederhanakan pertanyaan dengan cara yang lebih aman
        return ''.join(c.lower() if c.isalnum() else ' ' for c in question).strip()
