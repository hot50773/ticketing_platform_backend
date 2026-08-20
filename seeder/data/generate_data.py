import csv
import random
from datetime import datetime
from pathlib import Path

# Base data sets
tc_adjectives = [
    "快樂的", "憂鬱的", "奔跑的", "睡覺的", "閃耀的", "慵懶的", "瘋狂的", "溫柔的", "傲嬌的", "呆萌的",
    "沉默的", "熱情的", "孤獨的", "帥氣的", "美麗的", "可愛的", "搞笑的", "霸氣的", "傲慢的", "低調的",
    "奢華的", "簡單的", "複雜的", "神秘的", "邪惡的", "正義的", "勇敢的", "懦弱的", "聰明的", "笨笨的",
    "幸運的", "倒霉的", "忙碌的", "閒適的", "飢餓的", "飽腹的", "冰冷的", "溫暖的", "甜甜的", "酸酸的",
    "鹹鹹的", "辣辣的", "苦澀的", "苦逼的", "努力的", "躺平的", "佛系的", "捲王之", "養生的", "蹦迪的"
]

tc_nouns = [
    "貓咪", "狗狗", "熊貓", "兔子", "倉鼠", "企鵝", "樹懶", "考拉", "狐狸", "柴犬",
    "橘貓", "程式設計師", "碼農", "社畜", "大學生", "高中生", "設計師", "產品經理", "運營", "廚師",
    "司機", "醫生", "護士", "老師", "學生", "肥宅", "少女", "少年", "大叔", "蘿莉",
    "御姐", "萌新", "大佬", "菜鳥", "鹹魚", "歐皇", "非酋", "學霸", "學渣", "吃貨"
]

tc_suffixes = [
    "去旅行", "在寫扣", "在摸魚", "正在睡覺", "想要放假", "要吃大餐", "愛喝珍奶", "愛喝可樂", "不吃香菜", "最愛吃辣"
]

tc_templates = [
    "我是{noun}", "{adj}的{noun}", "{noun}{suffix}", "{adj}的{noun}{suffix}"
]

sc_adjectives = [
    "快乐的", "忧郁的", "奔跑的", "睡觉的", "闪耀的", "慵懒的", "疯狂的", "温柔的", "傲娇的", "呆萌的",
    "沉默的", "热情的", "孤独的", "帅气的", "美丽的", "可爱的", "搞笑的", "霸气的", "傲慢的", "低调的",
    "奢华的", "简单的", "复杂的", "神秘的", "邪恶的", "正义的", "勇敢的", "懦弱的", "聪明的", "笨笨的",
    "幸运的", "倒霉的", "忙碌的", "闲适的", "饥饿的", "饱腹的", "冰冷的", "温暖的", "甜甜的", "酸酸的",
    "咸咸的", "辣辣的", "苦涩的", "苦逼的", "努力的", "躺平的", "佛系的", "卷王之", "养生的", "蹦迪的"
]

sc_nouns = [
    "猫咪", "狗狗", "熊猫", "兔子", "仓鼠", "企鹅", "树懒", "考拉", "狐狸", "柴犬",
    "橘猫", "程序员", "码农", "社畜", "大学生", "高中生", "设计师", "产品经理", "运营", "厨师",
    "司机", "医生", "护士", "老师", "学生", "肥宅", "少女", "少年", "大叔", "萝莉",
    "御姐", "萌新", "大佬", "菜鸟", "咸鱼", "欧皇", "非酋", "学霸", "学渣", "吃货"
]

sc_suffixes = [
    "去旅行", "在写代码", "在摸鱼", "正在睡觉", "想要放假", "要吃大餐", "爱喝珍奶", "爱喝可乐", "不吃香菜", "最爱吃辣"
]

sc_templates = [
    "我是{noun}", "{adj}的{noun}", "{noun}{suffix}", "{adj}的{noun}{suffix}"
]

# Generate unique names
nicknames = set()
target_count = 10000

while len(nicknames) < target_count:
    is_traditional = random.choice([True, False])
    if is_traditional:
        template = random.choice(tc_templates)
        adj = random.choice(tc_adjectives)
        noun = random.choice(tc_nouns)
        suffix = random.choice(tc_suffixes)
    else:
        template = random.choice(sc_templates)
        adj = random.choice(sc_adjectives)
        noun = random.choice(sc_nouns)
        suffix = random.choice(sc_suffixes)
        
    nickname = template.format(adj=adj, noun=noun, suffix=suffix)
    if random.random() < 0.2:
        nickname += str(random.randint(1, 9999))
    if len(nickname) <= 100:
        nicknames.add(nickname)

nicknames_list = list(nicknames)
random.shuffle(nicknames_list)

# current_date = datetime.now().strftime("%Y%m%d")

current_dir = Path(__file__).resolve().parent

# Version 1: Excel compatible (with BOM, standard quoting)
file_excel = current_dir / f"nicknames_excel.csv"
with open(file_excel, mode='w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["nick_name"])
    for name in nicknames_list:
        writer.writerow([name])

# Version 2: Standard developer version (No BOM, all quoted, standard RFC 4180)
file_std = current_dir / f"nicknames.csv"
with open(file_std, mode='w', encoding='utf-8', newline='') as f:
    # Explicit quoting for all fields to avoid parser misinterpretation
    writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(["nick_name"])
    for name in nicknames_list:
        writer.writerow([name])

print(f"Generated Excel version: {file_excel}")
print(f"Generated Standard version: {file_std}")
