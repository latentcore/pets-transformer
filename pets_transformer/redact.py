"""Free-text redaction: find personal data in Finnish / Swedish / English text and replace it with typed placeholders.

Hybrid of (1) validators with check digits and formats (HETU, IBAN, cards, phones, IPs, plates, IMEI),
(2) spoken-number sequences ("nolla neljä viisi …", "noll fyra …"), spelled e-mails ("piste", "ät"),
(3) cue words that type a nearby value (hetu:, asiakasnumero, salasana, käyttäjätunnus, Työpaikka, Potilas:),
(4) gazetteers built only from the PUBLIC adversary files (names, streets, municipalities, places),
(5) structural patterns: dates, ages, addresses, organisation suffixes (Oy, Ab, Ky, Tmi, ry), signatures.

Output per record: spans [{start, end, label}] (metadata/taxonomy.md span labels) and the redacted text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

LABEL_PRIORITY = ["NATIONAL_ID", "IBAN", "EMAIL", "URL", "IP_ADDRESS", "DEVICE_ID", "PAYMENT_CARD", "PHONE",
                  "ACCOUNT_NUMBER", "PROPERTY_ID", "LICENSE_PLATE", "SECRET", "USERNAME", "ADDRESS", "ORG", "PERSON",
                  "DATE", "AGE", "LOCATION", "HEALTH"]
IBAN_COUNTRIES = {"FI", "SE", "EE", "DE", "NO", "DK", "GB", "NL", "FR", "ES", "IT", "LT", "LV", "PL", "BE", "AT", "CH", "IE",
                  "PT", "LU", "IS", "CZ", "SK", "HU", "RO", "BG", "HR", "SI", "GR", "CY", "MT", "AX", "TR", "UA"}
PRIO = {l: i for i, l in enumerate(LABEL_PRIORITY)}

DIGIT_WORDS = {
    "fi": ["nolla", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan", "yhdeksän"],
    "sv": ["noll", "ett", "två", "tre", "fyra", "fem", "sex", "sju", "åtta", "nio"],
    "en": ["zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
}
ALL_DIGIT_WORDS = sorted({w for ws in DIGIT_WORDS.values() for w in ws}, key=len, reverse=True)
SEP_WORDS = ["viiva", "streck", "dash", "plus", "miinus", "minus", "bindestreck", "väliviiva", "tavuviiva", "hyphen"]
_DW = "|".join(ALL_DIGIT_WORDS + SEP_WORDS)
SPOKEN = re.compile(rf"\b(?:{_DW})(?:[\s,.;:/-]+(?:{_DW}|[a-zåäö]{{1,3}}))*(?:[\s,.;:/-]+(?:{_DW}))\b(?:[\s,]+[a-zåäö]{{1,3}}\b)?", re.I)
DIGIT_WORD_RX = re.compile(r"\b(?:" + "|".join(ALL_DIGIT_WORDS) + r")\b", re.I)

CUE_NATIONAL_ID = re.compile(r"(hetu|henkilötunnu|henkilotunnu|sotu\b|personnummer|personbeteckning|personal identity|"
                             r"social security|identity code|id code|id-?number|tunnuksen|loppuosa|syntymäaika)", re.I)
CUE_PHONE = re.compile(r"(puhelin|puh\.|\bsoitt|telefon|\bring|mobiili|gsm|phone|\bcall|numeroon|numeroni|numerosta|"
                       r"nummer|takaisinsoitto|tavoittaa|yhteysnumero|reach me)", re.I)
CUE_PHONE_STRICT = re.compile(r"(puhelin\w*|puh\.|\bsoitt\w*|telefon\w*|\bring\w*|mobiili\w*|gsm|phone|\bcall\w*|numeroon|"
                              r"numerosta|takaisinsoitto|tavoittaa|yhteysnumero\w*|reach me|mobile|cell)", re.I)
CUE_PLATE = re.compile(r"(rekkari\w*|rekisteri\w*|\bplate\w*|registr\w*|\bauto\w*|\bbil\w*|\bcar\b|ajoneuvo\w*|fordon\w*|vehicle)", re.I)
CUE_ORG_LOWER = re.compile(r"(operaattori\w*|operator\w*|työpaikka\w*|työnantaja\w*|arbetsgivare|employer|works at|work at|"
                           r"jobbar på|töissä|(?:transfer|number|numero\w*|siirto\w*|siirtää|flytta|port\w*)\s+(?:my number\s+|mitt nummer\s+)?(?:from|från))", re.I)
CUE_ACCOUNT_STRICT = re.compile(r"(asiakasnum\w*|asiakasnro|asiakastunnu\w*|sopimusnum\w*|sopimus\w*|lasku\w*|lakun|tilausnum\w*|"
                                r"tilaus\w*|viitenum\w*|\bviite\b|customer number|customer id|contract number|invoice\w*|"
                                r"order number|reference number|kundnum\w*|avtalsnum\w*|avtal\w*|faktura\w*|ordernum\w*|"
                                r"referensnum\w*)", re.I)
STARTER_STOP = {"joku", "jokin", "kiitos", "hei", "moi", "terve", "thank", "thanks", "hello", "hi", "sorry", "the", "my",
                "your", "we", "our", "this", "that", "please", "okay", "ok", "yes", "no", "ei", "kyllä", "jag", "vi", "det",
                "ja", "nej", "tack", "hej", "god", "good", "morning", "i've", "i'm", "it's", "tv-boksi", "line", "link",
                "netti", "nettiyhteys", "laskuni", "laskussa", "liittymä", "liittymäni", "puhelin", "puhelimeni", "boksi",
                "reititin", "modeemi", "internet", "wifi", "aallokko", "aallokolta", "aallokossa", "aallokon", "aava",
                "asiakaspalvelu", "kundtjänst", "support", "meiltä", "hienoa", "olen", "selvä", "kiva", "hyvä", "okei",
                "ymmärrän", "anteeksi", "pahoittelut", "valitettavasti", "tervetuloa", "welcome", "kiitos", "tack",
                "huomenta", "iltaa", "päivää", "moikka", "terve", "hyvää", "voinko", "voisitteko", "voitteko", "onko",
                "mikä", "mitä", "miten", "milloin", "missä", "kuka", "vad", "hur", "när", "var", "vem", "what", "how",
                "when", "where", "who", "why", "could", "would", "can", "is", "are", "do", "does", "did", "have", "has"}
CUE_IBAN = re.compile(r"(iban|tilinumero|\btili\w*|konto\w*|account number|bank account|tilille)", re.I)
CUE_CARD = re.compile(r"(kortti\w*|kortin|\bcard\b|\bkort\w*|visa|mastercard|maksukort\w*|luottokort\w*)", re.I)
CUE_ACCOUNT = re.compile(r"(asiakasnum\w*|asiakasnro|asiakastunnu\w*|sopimusnum\w*|sopimusnro|sopimuksen numero|sopimus\w*|"
                         r"lasku\w*|tilausnum\w*|tilauksen|tilaus\w*|viitenum\w*|viite\b|customer number|customer id|"
                         r"contract number|invoice\w*|order number|reference number|kundnum\w*|avtalsnum\w*|avtal\w*|"
                         r"faktura\w*|ordernum\w*|referensnum\w*)", re.I)
CUE_SECRET = re.compile(r"(salasana\w*|lösenord\w*|password\w*|passcode|pin-?koodi\w*|\bpin\b|\bpuk\b|wifi-?salasana|wlan-?salasana|"
                        r"turvakoodi\w*|tunnusluku\w*|\bkoodi on\b|\bkoden är\b|\bcode is\b)", re.I)
CUE_USERNAME = re.compile(r"(käyttäjätunnu\w*|käyttäjänimi\w*|\btunnukseni\b|username|user name|\blogin\b|användarnamn\w*|"
                          r"inloggning\w*|kirjautumistunnu\w*)", re.I)
CUE_DEVICE = re.compile(r"(\bimei\w*|sarjanumero\w*|\bserial\w*|\biccid\w*|sim-?kortin numero|laitteen numero|serienummer)", re.I)
CUE_ORG = re.compile(r"(työpaikka|työnantaja|arbetsplats|arbetsgivare|employer|works at|work at|töissä|jobbar på|"
                     r"opiskelee|koulu|school|pankki|bank\b|operaattori|previous operator|edellinen operaattori)", re.I)
CUE_PERSON = re.compile(r"(potilas|patient|omainen|puoliso|vaimo|aviomies|mies|äiti|isä|tytär|poika|mukana|konsultoitu|"
                        r"saattaja\w*|yhteyshenkilö|kontaktperson|contact person|anhörig|maka|make|\bmor\b|\bfar\b|dotter|"
                        r"\bson\b|\bdr\.?|lääkäri|hoitaja|\bsh\b|\bth\b|\bel\b|huoltaja|edunvalvoja|nimellä|named|heter|"
                        r"nimeni on|my name is|this is|täällä puhuu|here speaking|tässä|\bhei\b|\bhej\b|\bhi\b|\bhello\b|"
                        r"terveisin|regards|\bt\.|ystävällisin terveisin|mvh|hälsningar|kiitos|thank you|thanks|tack)\s*[:,]?\s*$", re.I)
PLACE_CUE = re.compile(r"(tapahtui|tapaturma|sattui|kaatui|loukkaantui|hände|inträffade|skadade sig|happened at|fell at|"
                       r"at the|löydettiin|found at|matkalla|lomalla|\bi\b|\bpå\b|\bin\b|\bto\b|\bfrom\b|muutti|asuu|flyttar)\s+$", re.I)
PLACE_NOUNS = re.compile(r"(tori\w*|asema\w*|keskus\w*|satama\w*|ranta\w*|puisto\w*|halli\w*|kortteli\w*|kirkko\w*|koulu\w*|"
                         r"sairaala\w*|terveysasema\w*|kauppa\w*|market\w*|park\w*|station\w*|centrum|torget|stranden|hamnen|"
                         r"skolan|kyrkan|gamla stad\w*|vanha kaupunki|ostoskeskus\w*|jäähalli\w*|uimahalli\w*|areena\w*)", re.I)
SIGNATURE = re.compile(r"(?m)^\s*/?\s*([A-ZÅÄÖ][\wåäöÅÄÖ-]+(?:\s[A-ZÅÄÖ][\wåäöÅÄÖ-]+){0,2}|[A-ZÅÄÖ]\.\s?[A-ZÅÄÖ][\wåäöÅÄÖ-]+)"
                       r"\s*,?\s*(?:LL|LKT|erikoislääkäri|el|EL|ayl|sh|th|lh|läkare|specialistläkare|sjuksköterska|MD|RN|"
                       r"physician|nurse|triage nurse|triagehoitaja|triagesjuksköterska)\.?\s*$")
RX = {
    "HETU": re.compile(r"(?<![\w])\d{6}\s?[-+A]\s?\d{3}\s?[0-9ABCDEFHJKLMNPRSTUVWXY](?![\w])", re.I),
    "ACCOUNT_LOOSE": re.compile(r"(?<![\d])[1-9]\d{7,9}(?![\d])"),
    "ONLY_DIGITS": re.compile(r"^\W*(\d[\d ]{2,}\d|\d{3,})\W*$"),
    "HETU_NOSEP": re.compile(r"(?<![\w])\d{9}[0-9ABCDEFHJKLMNPRSTUVWXY](?![\w])", re.I),
    "HETU_WORDSEP": re.compile(r"(?<![\w])\d{6}\s*(?:viiva|streck|dash|bindestreck|väliviiva|-|plus|\+)\s*\d{3}\s*[0-9a-zåäö](?![\w])", re.I),
    "HETU_PARTIAL": re.compile(r"(?<![\w])(?:\d{6}\s?[-+A]\s?\d{0,3}[0-9A-Y]?|[-+A]\d{3}[0-9A-Y]|\d{6})(?![\w])", re.I),
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "EMAIL_SPOKEN": re.compile(r"\b[\w-]{2,}(?:\s*(?:piste|punkt|dot)\s*[\w-]+)*\s*(?:ät|snabel-?a|\bat\b|miukumauku|@)\s*[\w-]+(?:\s*(?:piste|punkt|dot)\s*[\w-]+)+", re.I),
    "IBAN": re.compile(r"(?<![\w])[A-Za-z]{2}\s?\d{2}(?:\s?[A-Za-z0-9]{4}){3,7}(?:\s?[A-Za-z0-9]{1,4})?(?![\w])"),
    "CARD16": re.compile(r"(?<![\d])(?:\d{4}[\s-]?){3}\d{4}(?![\d])"),
    "CARD_ANY": re.compile(r"(?<![\d])(?:\d[\s-]?){13,19}(?![\d])"),
    "PHONE": re.compile(r"(?<![\w+])(?:\+358[\s-]?|0)\d{1,3}(?:[\s-]?\d{2,4}){2,4}(?![\w])"),
    "IPV4": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)"),
    "PLATE": re.compile(r"(?<![\w])[A-ZÅÄÖ]{2,3}-\d{1,3}(?![\w])"),
    "URL": re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I),
    "IMEI": re.compile(r"(?<![\d])\d{15}(?![\d])"),
    "IMEI_SPACED": re.compile(r"(?<![\d])\d{2}[\s-]?\d{6}[\s-]?\d{6}[\s-]?\d(?![\d])"),
    "ICCID": re.compile(r"(?<![\d])\d{19,20}(?![\d])"),
    "PROPERTY": re.compile(r"(?<![\d])\d{1,3}-\d{1,3}-\d{1,4}-\d{1,4}(?![\d])"),
    "DATE_FI": re.compile(r"(?<![\d.])\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?(?![\d])"),
    "DATE_ISO": re.compile(r"(?<![\d])\d{4}-\d{2}-\d{2}(?![\d])"),
    "DATE_SLASH": re.compile(r"(?<![\d])\d{1,2}/\d{1,2}/\d{2,4}(?![\d])"),
    "DATE_WORD": re.compile(r"(?<![\w])(?:\d{1,2}(?:st|nd|rd|th)?\.?\s+(?:tammikuu|helmikuu|maaliskuu|huhtikuu|toukokuu|kesäkuu|"
                           r"heinäkuu|elokuu|syyskuu|lokakuu|marraskuu|joulukuu|januari|februari|mars|april|maj|juni|juli|"
                           r"augusti|september|oktober|november|december|january|february|march|may|june|august)[a-zåäö]*"
                           r"(?:\s+\d{4})?|(?:january|february|march|april|may|june|july|august|september|october|november|"
                           r"december)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)(?![\w])", re.I),
    "AGE": re.compile(r"(?<![\d,.])(\d{1,3})(?=\s?(?:-?\s?v\b|-?vuotias|-?vuotiaan|-?vuotiaalla|-?vuotiaana|-?v\.|vuotta|-?år\b|"
                     r"-?åring|-?årig|\s?years?\s?old|-?year-?old|\s?yo\b|-?åriga|\s?vuoden|-?vuotta|-?kk\b|\s?months? old|-?mån))", re.I),
    "AGE_CUE": re.compile(r"(?:ikä|olen|ålder|jag är|i am|i'm|age|years old:?)\s*:?\s*(\d{1,3})(?![\d])", re.I),
    "ORG": re.compile(r"(?<![\w])(?:[A-ZÅÄÖ][\wåäöÅÄÖ&'’-]+\s){1,3}(?:Oy Ab|Oy|Ab|Ky|Tmi|Oyj|ry|AB|Ltd|Inc|GmbH|Kommandiittiyhtiö)(?![\w])"),
    "ADDRESS": re.compile(r"(?<![\w])[A-ZÅÄÖa-zåäö][\wåäöÅÄÖ-]*(?:katu|tie|kuja|polku|ranta|rinne|väylä|kaari|raitti|puistikko|tori|"
                         r"gatan|vägen|gränd|stigen|strand|backen|väg|gata|ringen|rinki|tanhua|piha|mäki)\s+\d{1,4}\s?[A-Za-z]?(?:\s?[A-Z]\s?\d{1,3})?"
                         r"(?:,?\s*\d{5}\s+[A-ZÅÄÖa-zåäö][\wåäö]+)?", re.I),
    "POSTTOWN": re.compile(r"(?<![\d])\d{5}\s+[A-ZÅÄÖ][a-zåäö]+(?![\w])"),
}
FI_SUFFIX = ["lle", "lla", "llä", "lta", "ltä", "ssa", "ssä", "sta", "stä", "ksi", "kin", "han", "hän", "ta", "tä", "na", "nä",
             "an", "än", "in", "en", "n", "a", "ä", "s", "t"]
LOCATIVE = ("lle", "lla", "llä", "lta", "ltä", "ssa", "ssä", "sta", "stä", "on", "un", "en", "een", "seen", "hun")
COUNTRIES = ["thaimaa", "espanja", "ruotsi", "viro", "venäjä", "saksa", "britannia", "englanti", "italia", "kreikka", "turkki",
             "norja", "tanska", "puola", "ranska", "portugali", "kanaria", "tenerife", "mallorca", "usa", "amerikka", "kypros",
             "thailand", "spanien", "sverige", "estland", "ryssland", "tyskland", "storbritannien", "england", "grekland",
             "turkiet", "norge", "danmark", "polen", "frankrike", "portugal", "sweden", "estonia", "germany", "greece", "turkey",
             "norway", "denmark", "poland", "france", "italy", "finland", "suomi", "helsinki", "tampere", "oulu", "vaasa",
             "jyväskylä", "lahti", "kuopio", "tukholma", "stockholm", "tallinna", "tallinn", "lontoo", "london"]
HEALTH_WORDS = re.compile(r"\b(kuulolaite\w*|diabete[sk]\w*|epilepsi\w*|masennu\w*|sydänsairau\w*|syöpä\w*|syövä\w*|allergi\w*|"
                          r"astma\w*|raskaana|raskau\w*|liikuntarajoit\w*|pyörätuoli\w*|näkövamma\w*|kuulovamma\w*|dementi\w*|"
                          r"muistisairau\w*|hörapparat|diabetes|epilepsi|depression|cancer|gravid|rullstol\w*|"
                          r"hearing aid|diabetic|wheelchair|pregnant|depressed|asthma|sairaalassa|sjukhus\w*|hospital\w*|"
                          r"lääkitys\w*|medicin\w*|medication|leikkau\w*|operation|vamma\w*|sairaslom\w*|sjukskriv\w*|sick leave)\b", re.I)
STOP_NAMES = {"onneksi", "toivon", "lunta", "aina", "vain", "oma", "ilta", "aamu", "kesä", "talvi", "kuu", "usko", "rauha",
              "ilo", "sade", "kaste", "halla", "turku", "meri", "tuuli", "sisko", "veli", "ja", "on", "ei", "se", "hän", "tai",
              "oli", "kun", "että", "mutta", "niin", "nyt", "jos", "kyllä", "joo", "okei", "moi", "hei", "terve", "ok",
              "status", "osasto", "tila", "arvio", "hoito", "koti", "kotiin", "mukana", "sitten", "myös", "the", "and",
              "with", "this", "that", "for", "you", "your", "det", "och", "att", "jag", "har", "kan", "med", "för", "som",
              "van", "den", "min", "aallon", "terveysasema", "päivystys", "kontrolli", "tulosyy", "esitiedot", "nykytila",
              "perussairaudet", "lääkitys", "asuu", "omainen", "puoliso", "potilas", "patient"}
NAME_COLUMNS = {"name", "name_mentioned", "full_name", "player", "player_name", "student", "student_name", "graduate",
                "staff_name", "employee", "employee_name", "member", "member_name", "display_name", "customer_name"}


@dataclass
class TextGazetteers:
    first_names: set = field(default_factory=set)
    surnames: set = field(default_factory=set)
    streets: set = field(default_factory=set)
    municipalities: set = field(default_factory=set)
    places: set = field(default_factory=set)

    @classmethod
    def from_root(cls, root: Path) -> "TextGazetteers":
        g = cls()
        adv = root / "adversary"
        for f in sorted(adv.glob("*.csv")) if adv.exists() else []:
            try:
                d = pd.read_csv(f, dtype=str).fillna("")
            except Exception:  # noqa: BLE001
                continue
            for c in d.columns:
                cl = c.lower()
                if cl in NAME_COLUMNS:
                    for v in d[c]:
                        toks = [t for t in re.split(r"[\s,]+", str(v).strip()) if t]
                        if 2 <= len(toks) <= 4 and all(t[:1].isalpha() for t in toks):
                            g.first_names.add(toks[0].lower())
                            for part in toks[-1].split("-"):
                                if len(part) >= 3:
                                    g.surnames.add(part.lower())
                            for t in toks[1:-1]:
                                if t[:1].isupper():
                                    g.first_names.add(t.lower())
                        elif len(toks) == 1 and toks[0][:1].isupper() and cl == "display_name":
                            g.first_names.add(toks[0].lower())
                if cl in ("street_address", "address", "street"):
                    for v in d[c]:
                        m = re.match(r"^([A-Za-zÅÄÖåäö][\wåäöÅÄÖ-]+)\s+\d", str(v).strip())
                        if m:
                            g.streets.add(m.group(1).lower())
                if cl in ("city", "municipality", "profile_city", "place", "home_city", "town"):
                    g.municipalities |= {str(v).strip().lower() for v in d[c] if str(v).strip()}
                if cl in ("place_name", "place_tag", "venue", "neighbourhood", "location"):
                    g.places |= {str(v).strip().lower() for v in d[c] if len(str(v).strip()) >= 5 and not str(v).strip().isdigit()}
        g.first_names = {n for n in g.first_names if n.isalpha() and 2 <= len(n) <= 15} - STOP_NAMES
        g.surnames = {n for n in g.surnames if n.isalpha() and 3 <= len(n) <= 20} - STOP_NAMES
        g.municipalities -= {""}
        return g


_WORD = re.compile(r"[A-Za-zÅÄÖåäöÉéÜüØøÆæ][\wåäöÅÄÖéÉüÜøØæÆ'’-]*", re.UNICODE)


def lower_text_hint(t: str) -> bool:
    words = _WORD.findall(t)
    return sum(1 for w in words if w[:1].isupper()) <= max(1, len(words) // 12)


DISTRICTS = ["hirvensalo", "runosmäki", "varissuo", "halinen", "nummi", "kupittaa", "itäharju", "länsikeskus", "pansio",
             "uittamo", "moikoinen", "kakskerta", "satava", "ruissalo", "port arthur", "martti", "puistomäki", "nättinummi",
             "pääskyvuori", "lauste", "ilpoinen", "vasaramäki", "luolavuori", "jyrkkälä", "härkämäki", "räntämäki", "paattinen",
             "yli-maaria", "maaria", "jäkärlä", "koroinen", "raunistula", "pahaniemi", "perno", "artukainen", "kaistarniemi",
             "suikkila", "kärsämäki", "oriketo", "metsämäki", "katariina", "vähäheikkilä", "mäntymäki", "pitkämäki",
             "pohjola", "kaerla", "teräsrautela", "nauvo", "korppoo", "houtskari", "parainen", "nagu", "korpo", "pargas",
             "littoinen", "kuninkoja", "kurala", "harittu", "ispoinen", "skanssi", "hepokulta", "kohmo", "haritun"]
COUNTRIES_EXTRA = ["sveitsi", "schweiz", "switzerland", "itävalta", "österrike", "austria", "hollanti", "alankomaat",
                   "nederländerna", "netherlands", "belgia", "belgien", "belgium", "irlanti", "irland", "ireland", "islanti",
                   "island", "iceland", "latvia", "lettland", "liettua", "litauen", "lithuania", "unkari", "ungern", "hungary",
                   "tšekki", "tjeckien", "czechia", "kroatia", "kroatien", "croatia", "bulgaria", "bulgarien", "romania",
                   "rumänien", "kiina", "kina", "china", "japani", "japan", "intia", "indien", "india", "egypti", "egypten",
                   "egypt", "marokko", "marocko", "morocco", "tunisia", "dubai", "arabiemiraatit", "kanada", "canada",
                   "meksiko", "mexiko", "mexico", "brasilia", "brasilien", "brazil", "australia", "australien", "spain",
                   "russia", "ukraina", "ukraine", "serbia", "slovakia", "slovenia", "malta", "kreeta", "kreta", "crete",
                   "rodos", "rhodes", "lappi", "lapland", "ahvenanmaa", "åland", "saariselkä", "levi", "ylläs", "rovaniemi",
                   "turki", "espanj", "ruotsi", "saks", "kreik", "italia", "ranska", "portugali", "unkari", "kiina", "japani"]


def _strip_fi(tok: str) -> list[str]:
    """Candidate base forms of a Finnish-inflected token."""
    out = [tok]
    for s in FI_SUFFIX:
        if tok.endswith(s) and len(tok) - len(s) >= 3:
            out.append(tok[: -len(s)])
    return out


def _luhn(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    tot, alt = 0, False
    for x in reversed(d):
        if alt:
            x *= 2
            if x > 9:
                x -= 9
        tot += x
        alt = not alt
    return len(d) >= 13 and tot % 10 == 0


class Redactor:
    def __init__(self, gaz: TextGazetteers, clinical: bool = False):
        self.g = gaz
        self.clinical = clinical  # clinical notes: HEALTH label is not used (the whole note is clinical)
        places = sorted((p for p in gaz.places if len(p) >= 5), key=len, reverse=True)
        self.place_rx = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(p) for p in places) + r")[\wåäö]*", re.I) if places else None

    @staticmethod
    def _cue_before(text: str, start: int, cue: re.Pattern, window: int = 70) -> bool:
        return bool(cue.search(text[max(0, start - window):start]))

    def _is_first(self, low: str) -> bool:
        return len(low) >= 3 and any(b in self.g.first_names for b in _strip_fi(low))

    def _is_last(self, low: str) -> bool:
        parts = [p for p in low.split("-") if p]
        return len(low) >= 4 and all(any(b in self.g.surnames for b in _strip_fi(p)) for p in parts) and bool(parts)

    def _is_muni(self, low: str) -> bool:
        if len(low) < 4 or low in STARTER_STOP:
            return False
        return any(b in self.g.municipalities or b in COUNTRIES or b in COUNTRIES_EXTRA or b in DISTRICTS for b in _strip_fi(low))

    def find(self, text: str, prev_label: str | None = None) -> list[dict]:  # noqa: C901
        """prev_label: label of a number the previous turn of the same conversation ended with (continuations)."""
        spans: list[dict] = []
        add = lambda s, e, l: spans.append({"start": s, "end": e, "label": l})  # noqa: E731
        t = text

        # ---- identifiers with fixed formats
        for m in RX["HETU"].finditer(t):
            add(m.start(), m.end(), "NATIONAL_ID")
        for m in RX["HETU_WORDSEP"].finditer(t):
            add(m.start(), m.end(), "NATIONAL_ID")
        for m in RX["HETU_NOSEP"].finditer(t):
            v = m.group()
            if not (1 <= int(v[0:2]) <= 31 and 1 <= int(v[2:4]) <= 12):
                continue
            if v.isdigit() and v[0] == "0" and not self._cue_before(t, m.start(), CUE_NATIONAL_ID, 120):
                continue  # 10 digits starting with 0 is a phone number unless an ID cue says otherwise
            add(m.start(), m.end(), "NATIONAL_ID")
        for m in RX["HETU_PARTIAL"].finditer(t):
            v = m.group()
            if v.startswith(("-", "+", "A", "a")) or self._cue_before(t, m.start(), CUE_NATIONAL_ID, 120) or \
                    (len(v) > 6 and "-" in v):
                if not (v.isdigit() and len(v) == 6 and not self._cue_before(t, m.start(), CUE_NATIONAL_ID, 120)):
                    add(m.start(), m.end(), "NATIONAL_ID")
        for m in RX["EMAIL"].finditer(t):
            add(m.start(), m.end(), "EMAIL")
        for m in RX["EMAIL_SPOKEN"].finditer(t):
            add(m.start(), m.end(), "EMAIL")
        for m in RX["URL"].finditer(t):
            add(m.start(), m.end(), "URL")
        for m in RX["IBAN"].finditer(t):
            v = re.sub(r"\s", "", m.group())
            if len(v) >= 15 and v[:2].upper() in IBAN_COUNTRIES and v[2:4].isdigit():
                add(m.start(), m.end(), "IBAN")
        for m in RX["CARD_ANY"].finditer(t):
            digits = re.sub(r"\D", "", m.group())
            if _luhn(m.group()) or RX["CARD16"].fullmatch(m.group().strip()):
                if (len(digits) == 15 and not digits.startswith(("34", "37"))) or self._cue_before(t, m.start(), CUE_DEVICE, 60):
                    add(m.start(), m.end(), "DEVICE_ID")  # IMEIs are 15 Luhn-valid digits too
                else:
                    add(m.start(), m.end(), "PAYMENT_CARD")
        for key in ("ICCID", "IMEI", "IMEI_SPACED"):
            for m in RX[key].finditer(t):
                if key != "IMEI_SPACED" or self._cue_before(t, m.start(), CUE_DEVICE, 80):
                    add(m.start(), m.end(), "DEVICE_ID")
        for m in RX["PHONE"].finditer(t):
            digits = re.sub(r"\D", "", m.group())
            if 7 <= len(digits) <= 13 and not re.search(r"^[€$£%]|^\.\d", t[m.end():m.end() + 2]):
                add(m.start(), m.end(), "PHONE")
        for m in RX["IPV4"].finditer(t):
            if not m.group().startswith(("192.168.", "10.", "127.", "0.")):
                add(m.start(), m.end(), "IP_ADDRESS")
        for m in RX["PLATE"].finditer(t):
            add(m.start(), m.end(), "LICENSE_PLATE")
        for m in re.finditer(r"(?<![\w])[a-zåäö]{2,3}-\d{1,3}(?![\w])", t):
            if self._cue_before(t, m.start(), CUE_PLATE, 70):
                add(m.start(), m.end(), "LICENSE_PLATE")
        for m in RX["PROPERTY"].finditer(t):
            add(m.start(), m.end(), "PROPERTY_ID")

        # ---- spoken number sequences: typed by cue words and shape
        for m in SPOKEN.finditer(t):
            seq = m.group()
            n_digits = len(DIGIT_WORD_RX.findall(seq))
            if n_digits < 4:
                continue
            before = t[max(0, m.start() - 90):m.start()]
            has_sep = bool(re.search(r"\b(viiva|streck|dash|plus|miinus|minus|bindestreck|hyphen)\b", seq, re.I))
            starts_zero = bool(re.match(r"(nolla|noll|zero|oh)\b", seq, re.I))
            if CUE_IBAN.search(before) or re.search(r"\bf\s?i\s*$", before, re.I):
                add(m.start(), m.end(), "IBAN")
            elif CUE_NATIONAL_ID.search(before) or has_sep:
                add(m.start(), m.end(), "NATIONAL_ID")
            elif CUE_DEVICE.search(before):
                add(m.start(), m.end(), "DEVICE_ID")
            elif CUE_CARD.search(before) and n_digits >= 12:
                add(m.start(), m.end(), "PAYMENT_CARD")
            elif CUE_ACCOUNT_STRICT.search(before) and not starts_zero:
                add(m.start(), m.end(), "ACCOUNT_NUMBER")
            elif prev_label == "DEVICE_ID" and n_digits >= 12:
                add(m.start(), m.end(), "DEVICE_ID")
            elif CUE_PHONE.search(before) or starts_zero:
                add(m.start(), m.end(), "PHONE")
            elif n_digits >= 9:
                add(m.start(), m.end(), "NATIONAL_ID")
            elif prev_label in ("NATIONAL_ID", "PHONE", "ACCOUNT_NUMBER", "IBAN", "PAYMENT_CARD", "DEVICE_ID"):
                add(m.start(), m.end(), prev_label)
            else:
                add(m.start(), m.end(), "PHONE")
        # a turn that is nothing but digits / digit words continues the number of the previous turn
        om = RX["ONLY_DIGITS"].match(t)
        if om:
            digits = re.sub(r"\D", "", om.group(1))
            lab = prev_label if prev_label in ("NATIONAL_ID", "PHONE", "ACCOUNT_NUMBER", "IBAN", "PAYMENT_CARD", "DEVICE_ID") else \
                ("NATIONAL_ID" if len(digits) == 6 else "PHONE" if len(digits) <= 9 else "ACCOUNT_NUMBER")
            add(om.start(1), om.end(1), lab)
        elif prev_label and DIGIT_WORD_RX.search(t) and re.fullmatch(r"\W*(?:" + "|".join(ALL_DIGIT_WORDS + SEP_WORDS) + r"|[\s,.;:/-]|[a-zåäö]{1,3}\b)+\W*", t, re.I):
            add(0, len(t.rstrip()), prev_label)
        # "numero on 050" / "the number is 044": a short number right after a generic number cue (phone read out in parts)
        for cm in re.finditer(r"\b(numero\w*|number|nummer|numrret|numeero)\s+(?:on|is|är|oli|was)\s+(\d[\d ]{0,12}\d|\d{2,})(?![\d])", t, re.I):
            if not self._cue_before(t, cm.start(), CUE_ACCOUNT_STRICT, 30) and not self._cue_before(t, cm.start(), CUE_NATIONAL_ID, 30):
                add(cm.start(2), cm.end(2), "PHONE")

        # ---- cue-typed digit values and tokens
        for cm in CUE_ACCOUNT.finditer(t):
            seg = t[cm.end():cm.end() + 45]
            vm = re.search(r"(\d[\d ]{4,}\d)", seg)
            if vm and vm.start(1) <= 22:
                s0 = cm.end() + vm.start(1)
                strict = bool(CUE_ACCOUNT_STRICT.fullmatch(cm.group()))
                if strict or not self._cue_before(t, s0, CUE_PHONE_STRICT, 30):
                    add(s0, cm.end() + vm.end(1), "ACCOUNT_NUMBER")
        for m in RX["ACCOUNT_LOOSE"].finditer(t):
            if self._cue_before(t, m.start(), CUE_ACCOUNT_STRICT, 60) or (m.group()[0] in "2345" and len(m.group()) >= 8 and not self._cue_before(t, m.start(), CUE_PHONE_STRICT, 40)):
                add(m.start(), m.end(), "ACCOUNT_NUMBER")
        for cm in CUE_PHONE_STRICT.finditer(t):
            seg = t[cm.end():cm.end() + 30]
            vm = re.search(r"^\W{0,12}?(?:on|is|är|oli|was|numero|number|nummer)?\W{0,3}(\d[\d ]{1,}\d)(?![\d])", seg)
            if vm and 3 <= len(re.sub(r"\D", "", vm.group(1))) <= 13:
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "PHONE")
        for cm in CUE_DEVICE.finditer(t):
            vm = re.search(r"(\d[\d ]{8,}\d)", t[cm.end():cm.end() + 60])
            if vm and vm.start(1) <= 30:
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "DEVICE_ID")
        if prev_label == "DEVICE_ID" or self._cue_before(t, len(t), CUE_DEVICE, len(t)):
            for m in re.finditer(r"(?<![\d])\d[\d ]{13,22}\d(?![\d])", t):
                if 14 <= len(re.sub(r"\D", "", m.group())) <= 20:
                    add(m.start(), m.end(), "DEVICE_ID")
        for cm in CUE_CARD.finditer(t):
            seg = t[cm.end():cm.end() + 50]
            vm = re.search(r"(?:loppu\w*|päätty\w*|ending|slutar|ends|last|viimeiset|sista)\D{0,12}(\d{4})(?![\d])", seg, re.I)
            if vm:
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "PAYMENT_CARD")
        for cm in CUE_SECRET.finditer(t):
            seg = t[cm.end():cm.end() + 40]
            vm = re.search(r"^\W{0,3}(?:(?:on|is|är|oli|was|olisi|ois|would|be|olkoon|blir|olisiko|varmaan|probably|maybe|kai|ehkä|"
                           r"siis|then|now|nyt|että|that|the|new|uusi|vanha|old|still|edelleen|muuten|btw)\s+){0,4}[\"'“”]?([^\s,.;:!?\"'“”]{4,})", seg, re.I)
            if vm:
                val = vm.group(1)
                if (re.search(r"\d", val) or re.search(r"[A-ZÅÄÖ]", val[1:]) or re.search(r"[^\w]", val)) and val.lower() not in ("unohtui", "unohdin", "vaihdettu"):
                    add(cm.end() + vm.start(1), cm.end() + vm.end(1), "SECRET")
        for cm in CUE_USERNAME.finditer(t):
            seg = t[cm.end():cm.end() + 40]
            vm = re.search(r"^\W{0,3}(?:(?:on|is|är|oli|was|there|here|siellä|täällä|my|mun|the|sinne|seems|to|be|would|should)\s+){0,5}([A-Za-z0-9][A-Za-z0-9_.@-]{3,})", seg, re.I)
            if vm and re.search(r"\d|[._@]", vm.group(1)):
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "USERNAME")

        # ---- dates and ages
        for key in ("DATE_FI", "DATE_ISO", "DATE_SLASH", "DATE_WORD"):
            for m in RX[key].finditer(t):
                if key == "DATE_FI":
                    before = t[max(0, m.start() - 7):m.start()].lower()
                    if re.search(r"(klo|kl\.|kello|at|@)\s*$", before):
                        continue
                    if re.fullmatch(r"\d{1,2}\.\d{2}", m.group()) and int(m.group().split(".")[1]) > 12:
                        continue
                add(m.start(), m.end(), "DATE")
        for m in RX["AGE"].finditer(t):
            if int(m.group(1)) <= 120:
                add(m.start(1), m.end(1), "AGE")
        for m in RX["AGE_CUE"].finditer(t):
            if int(m.group(1)) <= 120:
                add(m.start(1), m.end(1), "AGE")

        # ---- addresses and organisations
        for m in RX["ADDRESS"].finditer(t):
            add(m.start(), m.end(), "ADDRESS")
        for m in RX["POSTTOWN"].finditer(t):
            add(m.start(), m.end(), "ADDRESS")
        for m in RX["ORG"].finditer(t):
            s0 = m.start()
            lead = t[s0:m.end()].split()
            if lead and lead[0].lower().rstrip(":") in ("työpaikka", "työnantaja", "arbetsplats", "employer", "firma", "yritys"):
                s0 += len(lead[0]) + 1
            add(s0, m.end(), "ORG")
        for cm in CUE_ORG.finditer(t):
            seg = t[cm.end():cm.end() + 80]
            vm = re.match(r"[\wåäö]*\W{0,3}(?:(?:on|is|är|oli|was|the|my|mun|ollut|edelleen|still|nyt|now|siis)\s+){0,3}"
                          r"((?:[A-ZÅÄÖ][\wåäöÅÄÖ&'’-]+)(?:\s(?:[A-ZÅÄÖ][\wåäöÅÄÖ&'’-]+|of|and|&|och|ja)){0,5})", seg)
            if vm:
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "ORG")
                continue
        for cm in CUE_ORG_LOWER.finditer(t):
            seg = t[cm.end():cm.end() + 80]
            vm = re.match(r"[\wåäö]*\W{0,3}(?:(?:on|is|är|oli|was|the|my|mun|ollut|edelleen|still|nyt|now|siis|ni)\s+){0,3}"
                          r"([a-zåäöA-ZÅÄÖ][\wåäöÅÄÖ&'’-]{3,}(?:\s(?:telecom|oy|ab|ky|tmi|ltd|regional|hospital|bank|pankki|group|net|media|energia|"
                          r"logistics|logistiikka|sairaala|koulu|school|university|yliopisto|verkko|mobile|mobiili|centre|center|service)\b[\wåäö]*)*)", seg, re.I)
            if vm and vm.group(1).split()[0].lower() not in STARTER_STOP | {"jumissa", "vanha", "uusi", "toinen", "sama", "eri", "teille", "till", "er", "you", "sinne", "minulle", "meille", "numero", "nummer", "number"}:
                add(cm.end() + vm.start(1), cm.end() + vm.end(1), "ORG")
        for m in SIGNATURE.finditer(t):
            add(m.start(1), m.end(1), "PERSON")

        # ---- names, streets, municipalities, places (token walk over the text)
        words = [(w.group(), w.start(), w.end()) for w in _WORD.finditer(t)]
        n_caps = sum(1 for w, _, _ in words if w[:1].isupper())
        lower_text = n_caps <= max(1, len(words) // 12)  # chat / ASR style text without capitalisation
        i = 0
        while i < len(words):
            w, s, e = words[i]
            low = w.lower().strip("'’-")
            # abbreviated "T.Rauhala" / "T. Rauhala"
            if re.fullmatch(r"[A-ZÅÄÖ]", w) and i + 1 < len(words) and re.fullmatch(r"\.\s?", t[e:words[i + 1][1]]) and self._is_last(words[i + 1][0].lower()):
                add(s, words[i + 1][2], "PERSON")
                i += 2
                continue
            cue_person = bool(CUE_PERSON.search(t[max(0, s - 24):s]))
            is_first, is_last = self._is_first(low), self._is_last(low)
            capitalised = w[:1].isupper()
            if self._is_muni(low) and PLACE_CUE.search(t[max(0, s - 16):s]) and not cue_person:
                end = e
                if i + 1 < len(words) and PLACE_NOUNS.fullmatch(words[i + 1][0]) and t[e:words[i + 1][1]] == " ":
                    end = words[i + 1][2]
                add(s, end, "LOCATION")
                i += 1
                continue
            cue_ok = cue_person and capitalised and len(low) >= 3 and low.isalpha() and low not in STARTER_STOP
            if (is_first or is_last or cue_ok) and low not in STOP_NAMES and low not in STARTER_STOP:
                # a lone lowercase first name is only a name in lowercase chat text; muni-and-surname ambiguity -> location
                if not capitalised and not lower_text and not cue_person:
                    i += 1
                    continue
                if is_last and not is_first and self._is_muni(low) and not cue_person:
                    end = e
                    if i + 1 < len(words) and PLACE_NOUNS.fullmatch(words[i + 1][0]) and t[e:words[i + 1][1]] == " ":
                        end = words[i + 1][2]
                    add(s, end, "LOCATION")
                    i += 1
                    continue
                j = i + 1
                while j < len(words) and j - i < 4:
                    nw, ns, ne = words[j]
                    gap = t[words[j - 1][2]:ns]
                    if gap not in (" ", "-") :
                        break
                    nl = nw.lower()
                    if self._is_last(nl) or self._is_first(nl) or (nw[:1].isupper() and len(nw) > 2 and j == i + 1 and (is_first or cue_person) and nl not in STOP_NAMES):
                        j += 1
                    else:
                        break
                if is_last and not is_first and j == i + 1 and not capitalised and not cue_person:
                    i += 1
                    continue
                if cue_ok and not is_first and not is_last and j == i + 1:
                    # a name we only know from the cue: it must end the phrase ("Hei Minna,", "t. Bo +358…")
                    nxt = t[e:e + 1]
                    nxt_word = words[i + 1][0] if i + 1 < len(words) else ""
                    if not (nxt in ",!.:;?\n" or nxt == "" or (nxt_word and (nxt_word[:1].isupper() or not nxt_word[:1].isalpha())) or not nxt.strip()
                            and (not nxt_word or nxt_word[:1].isupper())):
                        i += 1
                        continue
                add(s, words[j - 1][2], "PERSON")
                i = j
                continue
            if low in self.g.streets and re.match(r"\s?\d", t[e:e + 3]):
                m = re.match(r"\s?\d{1,4}\s?[A-Za-z]?(?:\s?[A-Z]\s?\d{1,3})?", t[e:e + 14])
                add(s, e + (m.end() if m else 0), "ADDRESS")
                i += 1
                continue
            if self._is_muni(low):
                end, lab = e, "LOCATION"
                if i + 1 < len(words) and t[e:words[i + 1][1]] == " ":
                    nxt = words[i + 1][0]
                    if re.fullmatch(r"(terveyskeskus\w*|sairaala\w*|terveysasema\w*|koulu\w*|lukio\w*|yliopisto\w*|ammattikoulu\w*|"
                                    r"kaupunki\w*|kunta\w*|seurakunta\w*|hälsocentral\w*|sjukhus\w*|skola\w*|stad\w*|kommun\w*)", nxt, re.I):
                        end, lab = words[i + 1][2], "ORG"
                    elif PLACE_NOUNS.fullmatch(nxt):
                        end = words[i + 1][2]
                add(s, end, lab)
                i += 1
                continue
            if capitalised and len(low) >= 4 and PLACE_CUE.search(t[max(0, s - 16):s]) and not is_first and not is_last and low not in STARTER_STOP and not low.startswith("aallok"):
                end = e
                if i + 1 < len(words) and PLACE_NOUNS.fullmatch(words[i + 1][0]) and t[e:words[i + 1][1]] == " ":
                    end = words[i + 1][2]
                add(s, end, "LOCATION")
            i += 1
        if self.place_rx is not None:
            for m in self.place_rx.finditer(t):
                add(m.start(), m.end(), "LOCATION")
        if not self.clinical:
            for m in HEALTH_WORDS.finditer(t):
                add(m.start(), m.end(), "HEALTH")
        return self.resolve(spans)

    @staticmethod
    def resolve(spans: list[dict]) -> list[dict]:
        """Drop overlaps: keep higher-priority labels, then longer spans."""
        spans = sorted(spans, key=lambda s: (PRIO.get(s["label"], 99), -(s["end"] - s["start"]), s["start"]))
        kept: list[dict] = []
        for s in spans:
            if s["end"] <= s["start"]:
                continue
            if all(s["end"] <= k["start"] or s["start"] >= k["end"] for k in kept):
                kept.append(s)
        return sorted(kept, key=lambda s: s["start"])

    def redact(self, text: str, prev_label: str | None = None) -> tuple[str, list[dict]]:
        spans = self.find(text, prev_label)
        out, last = [], 0
        for s in spans:
            out.append(text[last:s["start"]])
            out.append(f"[{s['label']}]")
            last = s["end"]
        out.append(text[last:])
        return "".join(out), spans


NUMBER_LABELS = ("NATIONAL_ID", "PHONE", "ACCOUNT_NUMBER", "IBAN", "PAYMENT_CARD", "DEVICE_ID")


def redact_table(df: pd.DataFrame, text_col: str, id_col: str, red: Redactor, conv_col: str | None = None,
                 seq_col: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Redact every text; in conversations (conv_col/seq_col) a number read out over several turns keeps its label."""
    texts = df[text_col].fillna("").astype(str)
    conv = df[conv_col].astype(str) if conv_col and conv_col in df.columns else pd.Series([""] * len(df), index=df.index)
    red_texts, spans = [], {}
    last_conv, prev_label = None, None
    for rid, t, c in zip(df[id_col].astype(str), texts, conv):
        if c != last_conv:
            prev_label, last_conv = None, c
        r, s = red.redact(t, prev_label)
        red_texts.append(r)
        spans[rid] = s
        tail = [x for x in s if x["label"] in NUMBER_LABELS and x["end"] >= len(t.rstrip()) - 2]
        if tail:
            prev_label = tail[-1]["label"]
        elif re.search(r"\b(imei|sarjanumero|serial|iccid)\w*", t, re.I):
            prev_label = "DEVICE_ID"
        elif re.search(r"(numero|number|nummer|hetu|tunnus|iban|tili|konto)\w*\s*(on|is|är)?\s*[:?]?\s*$", t, re.I):
            pass  # cue at the end of the turn: keep the previous label for the continuation
        else:
            prev_label = None
    out = df.copy()
    out[text_col] = red_texts
    return out, spans


def write_spans(spans: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for rid, s in spans.items():
            f.write(json.dumps({"record_id": rid, "spans": s}, ensure_ascii=False) + "\n")
