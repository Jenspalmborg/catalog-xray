"""Describe a product by what it is, not what the brand calls it.

Branded names mislead text search: a shoe called "Pine Runner" finds Christmas trees,
a slipper called "Cloud Lounger" finds recliners. So each product is also described as a
generic product noun ("women's running shoes"), found in its category, tags,
name and description, which is what shoppers type and what the market uses.
"""

import re

# Specific phrases first within each line; matching prefers longer phrases.
NOUNS = """
running shoes, trail running shoes, walking shoes, hiking shoes, hiking boots, rain boots, snow boots, work boots,
chelsea boots, ankle boots, boots, sneakers, high tops, slip ons, slip-on shoes, loafers, flats, ballet flats, heels,
pumps, sandals, flip flops, slides, slippers, clogs, mules, golf shoes, tennis shoes, dress shoes, shoes, insoles,
shoelaces, no show socks, ankle socks, crew socks, compression socks, socks,
t-shirt, tee, tank top, polo shirt, shirt, blouse, hoodie, sweatshirt, sweater, cardigan, fleece, puffer jacket,
rain jacket, jacket, coat, vest, joggers, sweatpants, leggings, yoga pants, pants, trousers, chinos, jeans, shorts,
dress, skirt, jumpsuit, pajamas, pajama pants, sleepwear, robe, underwear, boxer briefs, briefs, boxers, boxer,
panties, thong, bra,
sports bra, swimsuit, swim trunks, bikini, beanie, baseball cap, cap, hat, scarf, gloves, mittens, base layer,
backpack, duffel bag, tote bag, crossbody bag, handbag, bag, wallet, belt, sunglasses, watch, smartwatch,
necklace, earrings, bracelet, ring, jewelry, umbrella, suitcase, luggage,
shampoo, conditioner, body wash, soap, moisturizer, face cream, serum, cleanser, sunscreen, deodorant, perfume,
cologne, lipstick, mascara, foundation, makeup, nail polish, hair dryer, hair straightener, flat iron, curling iron,
wig, razor, toothbrush,
toothpaste, lotion, lip balm,
eyeshadow palette, eyeshadow, palette, eyeliner, liquid eyeliner, eyeliner pen, eye pencil, kohl, lip pencil,
brow pen, brow pencil, brow gel, brow pomade, false lashes, lashes,
lash glue, lip gloss, lip liner, lip oil, liquid lipstick, lip kit, concealer, primer, setting spray, setting powder, powder,
bronzer, blush, highlighter, contour, contour stick, face palette, makeup brush, brush set, brush, makeup sponge,
beauty blender, makeup remover, micellar water, face mask, sheet mask, toner, face oil, eye cream, nail kit,
makeup bag, cosmetic bag, self tan, tanning mist, tanning drops, tan remover, night cream, day cream,
peeling pads, exfoliator, face scrub, lip scrub, balm, face mist, body lotion, body oil, eye patches, hair brush,
makeup kit, gift set,
pillow, throw pillow, blanket, throw blanket, duvet, comforter, sheets, bed sheets, towel, bath towel, mattress,
bed frame, lamp, table lamp, candle, rug, curtains, chair, office chair, table, desk, sofa, couch, shelf,
bookshelf, picture frame, vase, mirror, clock, storage bin, basket, hanger,
mug, coffee mug, cup, plate, bowl, glass, wine glasses, water bottle, tumbler, cutlery, knife, chef knife,
cutting board, frying pan, skillet, pan, pot, dutch oven, kettle, blender, coffee maker, espresso machine,
coffee grinder, toaster, air fryer, microwave, food storage, lunch box, vacuum, robot vacuum, air purifier,
humidifier, fan, heater,
headphones, earbuds, speaker, bluetooth speaker, charger, power bank, cable, phone case, phone, smartphone,
laptop, tablet, e-reader, monitor, keyboard, mouse, webcam, camera, security camera, drone, tv, router,
smart plug, game controller, video game, console,
yoga mat, dumbbells, kettlebell, resistance bands, exercise bike, treadmill, bike, bicycle, helmet, tent,
sleeping bag, camping chair, cooler, fishing rod, golf balls, golf clubs, tennis racket, ball, skateboard,
snowboard, skis, goggles, swim goggles,
coffee, coffee beans, coffee pods, tea, chocolate, candy, snacks, chips, cereal, pasta, olive oil, hot sauce,
protein powder, protein bar, energy drink, sparkling water, vitamins, supplements, wine,
lego, building set, puzzle, board game, card game, doll, stuffed animal, plush, toy car, action figure, toy,
dog food, cat food, dog bed, cat litter, leash, collar, dog toy, cat toy, pet bed,
stroller, car seat, diapers, baby bottle, baby carrier, crib,
notebook, journal, pen, pencil, planner, stapler, printer paper, sticky notes, backpack, pencil case,
guitar, ukulele, keyboard piano, drum, microphone, headphones,
paint, paint brushes, sketchbook, yarn, sewing machine, glue
"""

PHRASES = sorted({p.strip() for p in NOUNS.replace("\n", " ").split(",") if p.strip()}, key=lambda p: -len(p))


def _pattern(phrase: str) -> re.Pattern:
    # Match singular or plural and either hyphenation: "slip on" ~ "slip-ons".
    parts = [
        re.escape(w).replace(r"\-", "[- ]?") for w in phrase.replace("-", " - ").replace(" - ", "-").split()
    ]
    body = r"\s+".join(parts)
    if body.endswith("s"):
        body = body[:-1] + "s?"
    return re.compile(rf"\b{body}(?:e?s)?\b", re.I)


PATTERNS = [(p, _pattern(p)) for p in PHRASES]


def gender(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(women|womens|women's|ladies|woman)\b", t):
        return "women's"
    if re.search(r"\b(men|mens|men's|man)\b", t):
        return "men's"
    if re.search(r"\b(kids|kid's|boys|girls|youth|children|toddler)\b", t):
        return "kids'"
    return ""


def describe(name: str, category: str | None, tags: list[str], description: str) -> tuple[str, str]:
    """Return (query, noun): e.g. ("women's running shoes", "running shoes").
    Category counts most, then tags and name, then the description."""
    # The product's own name outweighs the category it sits in: "Liquid Highlighter" under Foundation.
    fields = [(name, 3.5), (category or "", 3.0), (" ".join(tags), 2.0), (description[:400], 1.0)]
    scores = {}
    for phrase, pat in PATTERNS:
        score = sum(w * len(pat.findall(text)) for text, w in fields if text)
        if score:
            scores[phrase] = score + 0.5 * (len(phrase.split()) - 1)
    best = max(scores, key=scores.get) if scores else ""
    # A kind the product's own name states wins: "Liquid Highlighter" is a highlighter, whatever its category.
    named = {p: v for p, v in scores.items() if dict(PATTERNS)[p].search(name)}
    if named:
        best = max(named, key=lambda p: (named[p], len(p)))
    if best in PARENTS:
        children = {p: v for p, v in scores.items() if family(p) in PARENTS[best]}
        if children:
            best = max(children, key=children.get)
    # "Brush" alone is ambiguous: eyeshadow, crease or powder around it means a makeup brush.
    if best == "brush" and re.search(
        r"eye|shadow|crease|blend|foundation|powder|contour|kabuki|makeup|concealer|"
        r"lip|brow|highlight|blush|bronz",
        " ".join([name, category or "", " ".join(tags)]),
        re.I,
    ):
        best = "makeup brush"
    # Decide what kind of thing it is first, then use the most specific phrase of that
    # kind found anywhere: category "Shoes" + "running shoe" in the text -> "running shoes".
    if best:
        same_kind = [p for p in scores if head(p) == head(best) and len(p.split()) > len(best.split())]
        if same_kind:
            best = max(same_kind, key=scores.get)
    g = gender(" ".join([name, " ".join(tags)]))
    return (f"{g} {best}".strip() if best else ""), best


def singular(w: str) -> str:
    w = w.lower()
    if len(w) <= 3 or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes", "zes", "sses")):
        return w[:-2]
    return w[:-1] if w.endswith("s") else w


# Different words, same kind of product.
SYNONYMS = {
    "moisturizer": "cream",
    "lip pencil": "lip liner",
    "moisturiser": "cream",
    "exfoliant": "exfoliator",
    "t-shirt": "tee",
    "trainer": "sneaker",
    "trouser": "pant",
    "jogger": "sweatpant",
    "couch": "sofa",
    "earphone": "earbud",
    "tennis shoe": "sneaker",
    "bicycle": "bike",
    "smartphone": "phone",
}


def head(noun: str) -> str:
    """The word a matching product must contain: 'running shoes' -> 'shoe', 'pajama pants' -> 'pant'."""
    h = singular(noun.split()[-1]) if noun else ""
    return SYNONYMS.get(h, h)


def has_head(text: str, h: str) -> bool:
    return bool(h) and re.search(rf"\b{re.escape(h)}(e?s)?\b", text.lower()) is not None


# Kinds that answer the same need, so "sneakers" and "running shoes" count as carried together.
FAMILIES = {
    "footwear": "shoe sneaker trainer boot sandal slipper slide loafer flat heel pump mule clog insole|slip on|slip-on|"
    "flip flop|high top",
    "socks": "sock",
    "tops": "tee t-shirt shirt blouse polo top hoodie sweatshirt sweater cardigan fleece|tank top|base layer",
    "bottoms": "pant trouser jogger sweatpant legging short jean chino skirt|yoga pant",
    "outerwear": "jacket coat vest puffer|rain jacket",
    "underwear": "underwear brief boxer bra|boxer brief|sports bra",
    "sleepwear": "pajama sleepwear robe",
    "headwear": "hat cap beanie",
    "swimwear": "swimsuit bikini|swim trunk",
    "bags": "backpack bag tote wallet suitcase luggage",
    "audio": "headphone earbud speaker",
    "bedding": "pillow blanket duvet comforter sheet mattress",
    "makeup tools": "sponge|makeup brush|brush set|makeup sponge|beauty blender|makeup bag|cosmetic bag",
    "hair tools": "comb|hair brush|hair dryer|hair straightener|flat iron|curling iron",
    "tanning": "|self tan|tanning mist|tanning drops|tan remover",
    "skincare": "serum moisturizer cleanser toner exfoliator balm|face cream|night cream|day cream|eye cream|face mask|"
    "sheet mask|face oil|face mist|peeling pad|face scrub|eye patch",
    "eyes": "eyeshadow eyeliner mascara lash lashes kohl|brow pencil|brow gel|brow pomade|brow pen|false lash|"
    "eye pencil|eyeliner pen",
    "lips": "lipstick|lip gloss|lip liner|lip pencil|lip oil|lip balm|lip kit",
    "face": "foundation concealer primer powder bronzer blush highlighter contour|setting spray",
}
# Vague kinds that should give way to a specific one when the text names it.
PARENTS = {
    "makeup": {"eyes", "lips", "face", "makeup tools", "skincare", "tanning"},
    "cosmetics": {"eyes", "lips", "face", "makeup tools", "skincare", "tanning"},
    "brush": {"makeup tools", "hair tools"},
    "shoes": {"footwear"},
    "clothes": {"tops", "bottoms", "outerwear", "underwear", "sleepwear"},
}
_FAMILY_KEYS = []
for fam, spec in FAMILIES.items():
    singles, *phrases = spec.split("|")
    _FAMILY_KEYS += [(p, fam) for p in phrases if p] + [(w, fam) for w in singles.split()]
_FAMILY_KEYS.sort(key=lambda kv: -len(kv[0]))


def family(noun: str) -> str:
    """'running shoes' -> 'footwear', 'crew socks' -> 'socks', 'flat iron' -> 'iron'.
    Two-word types ("slip on", "flip flop") are matched as phrases; otherwise the head word decides."""
    n = noun.lower()
    for key, fam in _FAMILY_KEYS:
        if " " in key or "-" in key:
            if re.search(rf"\b{re.escape(key)}(e?s)?\b", n):
                return fam
    h = head(noun)
    for key, fam in _FAMILY_KEYS:
        if key == h:
            return fam
    return h


def same_family(noun: str) -> list[str]:
    """Other vocabulary phrases of the same family: candidates to refine a generic noun."""
    fam = family(noun)
    return [p for p in PHRASES if p != noun and family(p) == fam]


# Departments: which product families belong to the same kind of shop. A gap only counts if it
# falls in a department the store sells in; shoppers of eyeliner also buy pens, but that's not beauty.
DEPARTMENTS = {
    "fashion": {
        "footwear",
        "socks",
        "tops",
        "bottoms",
        "outerwear",
        "underwear",
        "sleepwear",
        "headwear",
        "swimwear",
        "bags",
        "dress",
        "jumpsuit",
        "belt",
        "wallet",
        "sunglass",
        "watch",
        "jewelry",
        "necklace",
        "earring",
        "bracelet",
        "ring",
        "scarf",
        "glove",
        "mitten",
        "umbrella",
        "jean",
        "legging",
    },
    "beauty": {
        "eyes",
        "lips",
        "face",
        "makeup tools",
        "skincare",
        "tanning",
        "makeup",
        "perfume",
        "cologne",
        "nail polish",
        "lotion",
        "sunscreen",
        "deodorant",
        "soap",
        "body wash",
        "makeup kit",
        "gift set",
        "palette",
        "micellar water",
        "exfoliator",
        "cream",
        "balm",
        "polish",
    },
    "hair": {"hair tools", "shampoo", "conditioner"},
    "home": {
        "bedding",
        "towel",
        "lamp",
        "candle",
        "rug",
        "curtain",
        "chair",
        "table",
        "desk",
        "sofa",
        "shelf",
        "frame",
        "vase",
        "mirror",
        "clock",
        "basket",
        "mug",
        "cup",
        "plate",
        "bowl",
        "glass",
        "knife",
        "pan",
        "pot",
        "kettle",
        "blender",
        "maker",
        "machine",
        "toaster",
        "fryer",
        "vacuum",
        "purifier",
        "fan",
        "heater",
    },
    "electronics": {
        "audio",
        "charger",
        "cable",
        "phone",
        "laptop",
        "tablet",
        "monitor",
        "keyboard",
        "mouse",
        "webcam",
        "camera",
        "drone",
        "tv",
        "router",
        "console",
        "controller",
    },
    "office": {"notebook", "journal", "pen", "pencil", "planner", "stapler", "paper"},
}


def department(noun: str) -> str | None:
    fam = family(noun)
    for dept, members in DEPARTMENTS.items():
        if fam in members or head(noun) in members:
            return dept
    return None
