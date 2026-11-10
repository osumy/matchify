"""Persian/Arabic/Multilingual text and phone normalization engine for venue matching."""

import re
from typing import List

STOP_WORDS = {
    'کافه', 'رستوران', 'کافی', 'شاپ', 'قهوه', 'بار', 'لانژ', 'فست', 'فود', 'عمارت', 'خانه', 'سرای',
    'cafe', 'café', 'restaurant', 'coffee', 'shop', 'bar', 'lounge', 'fast', 'food', 'bakery',
    'شعبه', 'ایرانی', 'سنتی', 'مدرن', 'اصلی', 'مرکزی', 'بین', 'الملل', 'بین‌المللی',
    'تهران', 'اصفهان', 'مشهد', 'شیراز', 'تبریز', 'اهواز', 'کرج', 'قم',
    'رشت', 'کرمانشاه', 'ارومیه', 'زاهدان', 'یزد', 'قزوین', 'خرمشهر', 'آبادان',
    'ساری', 'گرگان', 'همدان', 'بندرعباس', 'بوشهر', 'زنجان', 'سنندج', 'کیش'
}

PERSIAN_TO_LATIN = {
    'آ': 'a', 'ا': 'a', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ث': 's',
    'ج': 'j', 'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'z',
    'ر': 'r', 'ز': 'z', 'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's',
    'ض': 'z', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'gh', 'ف': 'f',
    'ق': 'gh', 'ک': 'k', 'گ': 'g', 'ل': 'l', 'م': 'm', 'ن': 'n',
    'و': 'v', 'ه': 'h', 'ی': 'i', 'ي': 'i', 'ى': 'i', 'ك': 'k', 'ة': 'h',
    'ء': '', 'ئ': 'i', 'ؤ': 'o'
}

COMMON_TRANSLATIONS = {
    'مستر': 'mr', 'دوباره': 'dobiro', 'ناین': 'nine', 'بل': 'bell',
    'بیک': 'bake', 'پرسنت': 'percent', 'آیبیک': 'ibake', 'آواکو': 'avako',
    'بناک': 'bonak', 'آویژه': 'avizhe', 'سلوبون': 'celebon', 'گستو': 'gusto',
    'اگومان': 'agoman', 'کارزین': 'karzin', 'عباس': 'abbas', 'اقا': 'agha',
    'میلاد': 'milad', 'دنیز': 'deniz', 'روژه': 'rojeh', 'ویش': 'wish',
    'لیار': 'leymer', 'دیگر': 'andorra', 'نیلی': 'nili', 'پروچلو': 'prochelo',
    'ویونا': 'viuna', 'لامیز': 'lameiz', 'پاییز': 'autumn', 'طلایی': 'golden'
}


def normalize_text(text: str) -> str:
    """Exhaustive Persian/Arabic text normalizer handling ZWNJ, BiDi, Tatweel, diacritics."""
    if not text:
        return ""
    t = text.lower()
    # Strip BiDi directional formatting codes & ZWNJ/ZWSP
    t = re.sub(r'[\u200c\u200b\u200e\u200f\u202a-\u202e]', ' ', t)
    # Strip Tatweel (kashida)
    t = t.replace('\u0640', '')
    # Arabic to Persian normalization
    t = t.replace('ي', 'ی').replace('ى', 'ی').replace('ك', 'ک').replace('ة', 'ه').replace('ۀ', 'ه')
    t = t.replace('آ', 'ا').replace('أ', 'ا').replace('إ', 'ا').replace('ؤ', 'و').replace('ئ', 'ی')
    # Strip Arabic harakat/diacritics
    t = re.sub(r'[\u064B-\u065F\u0670]', '', t)
    # Persian & Arabic digits
    digits_map = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    for k, v in digits_map.items():
        t = t.replace(k, v)
    # Persian and standard punctuation
    t = re.sub(r'[\(\)\[\]\{\}\-_,\.\+:;/\&\\\|\'\"`!?؟،؛«»•–—…]', ' ', t)
    return ' '.join(t.split())


def clean_tokens(text: str) -> List[str]:
    """Splits normalized text into tokens and removes common venue/city stop words."""
    norm = normalize_text(text)
    tokens = norm.split()
    filtered = [w for w in tokens if w not in STOP_WORDS]
    return filtered if filtered else tokens


def to_phonetic_latin(tokens: List[str]) -> List[str]:
    """Transliterates Persian tokens into phonetic Latin representations for cross-language matching."""
    latin_tokens = []
    for tok in tokens:
        if tok in COMMON_TRANSLATIONS:
            latin_tokens.append(COMMON_TRANSLATIONS[tok])
            continue
        if all(ord(c) < 128 for c in tok):
            latin_tokens.append(tok)
            continue
        res = ''.join(PERSIAN_TO_LATIN.get(c, c) for c in tok)
        res = re.sub(r'[^a-z0-9]', '', res)
        res = re.sub(r'(.)\1+', r'\1', res)
        if res:
            latin_tokens.append(res)
    return latin_tokens


def normalize_phone(phone: str) -> str:
    """Normalizes phone number to standard digits (drops +98 / leading 0, returns last 8-10 digits)."""
    if not phone:
        return ""
    # Convert any eastern digits
    digits_map = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    p = phone
    for k, v in digits_map.items():
        p = p.replace(k, v)
    digits = re.sub(r'\D', '', p)
    if digits.startswith('98'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = digits[1:]
    return digits
