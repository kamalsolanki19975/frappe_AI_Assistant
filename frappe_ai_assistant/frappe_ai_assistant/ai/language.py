"""Language detection for English, Hindi (Devanagari) and Hinglish (Romanised Hindi).

Heuristic and deterministic on purpose: cheap, testable, and independent of the model.
Model quality per language must still be measured separately (Hinglish != Hindi).
"""

import re

LANGS = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish"}
PREF_TO_CODE = {"English": "en", "Hindi": "hi", "Hinglish": "hinglish"}

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[a-z]+")

# Common Romanised Hindi function words / verbs / question words (lower-case).
HINGLISH_WORDS = {
	"hai", "hain", "ho", "hoga", "hogi", "hoge", "tha", "thi", "the", "kya", "kyu", "kyun", "kyon", "kaise", "kaisa",
	"kaisi", "kab", "kahan", "kaha", "kitna", "kitni", "kitne", "kaun", "kis", "kisne", "kise", "mera", "meri",
	"mere", "mujhe", "main", "mai", "hum", "hume", "humein", "aap", "aapka", "aapki", "aapke", "tum", "tera", "teri",
	"uska", "uski", "unka", "yeh", "ye", "woh", "wo", "vo", "aur", "ya", "nahi", "nahin", "na", "haan", "ji", "bhi",
	"sirf", "abhi", "aaj", "kal", "parso", "pichhle", "pichle", "agle", "mahine", "mahina", "saal", "hafte",
	"din", "karo", "karna", "kariye", "karein", "kar", "kardo", "dikhao", "dikhaiye", "batao", "bataiye", "bata",
	"chahiye", "chaiye", "lagta", "raha", "rahi", "rahe", "gaya", "gayi", "gaye", "diya", "liya", "wala", "wali",
	"wale", "ka", "ki", "ke", "ko", "se", "mein", "me", "par", "pe", "tak", "liye", "lekin", "magar", "toh", "to",
	"kuch", "sab", "sabhi", "zyada", "jyada", "kam", "accha", "acha", "theek", "thik", "bahut", "bohot", "pending",
	"bhai", "yaar", "dost", "shukriya", "dhanyavad", "namaste", "kripya", "jaldi", "samjhao", "samjhaiye",
}
# Very common English words that also appear in the list above; ignored when scoring alone.
AMBIGUOUS = {"to", "the", "me", "main", "par", "pending", "ho", "na", "kam", "din", "se", "ya"}


def detect(text: str) -> str:
	"""Returns 'hi', 'hinglish' or 'en'."""
	if not text:
		return "en"
	deva = len(DEVANAGARI.findall(text))
	latin = len(LATIN.findall(text))
	if deva and deva >= 0.3 * (deva + latin):
		return "hi"
	words = WORD.findall(text.lower())
	if not words:
		return "en"
	hits = [w for w in words if w in HINGLISH_WORDS]
	strong = [w for w in hits if w not in AMBIGUOUS]
	if len(strong) >= 2 or (len(strong) >= 1 and len(hits) >= 2) or (len(words) <= 4 and len(strong) >= 1):
		return "hinglish"
	return "en"


def effective_language(text: str, preference: str | None, settings) -> str:
	"""User preference > admin default > detection. Disabled languages fall back to English."""
	pref = preference if preference and preference != "Auto" else None
	if not pref and settings.default_language and settings.default_language != "Auto":
		pref = settings.default_language
	code = PREF_TO_CODE.get(pref) if pref else None
	if not code:
		code = detect(text) if settings.auto_detect_language else "en"
	if code == "hi" and not settings.hindi_enabled:
		code = "en"
	if code == "hinglish" and not settings.hinglish_enabled:
		code = "en"
	return code


def instruction(code: str) -> str:
	return {
		"en": "Reply in clear, simple English.",
		"hi": "Reply in Hindi using Devanagari script. Keep SaarthiERP terms, document IDs, item codes, names and "
		"numbers exactly as they are (do not translate or transliterate them).",
		"hinglish": "Reply in natural Hinglish (conversational Hindi written in Roman/Latin script, mixed with "
		"English business words the way Indian office staff speak). Do not use Devanagari. Keep SaarthiERP terms, "
		"document IDs, item codes, names and numbers exactly as they are.",
	}[code]
