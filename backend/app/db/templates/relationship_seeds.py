"""默认 22 NPC 间的关系预设。

``world_gen.relationships.build_matrix`` 会先用 ``DEFAULT_PAIRS`` /
``DEFAULT_OWNER_PAIRS`` 强写明确关系，再用同区/同职业/同年龄段的弱默认
值把剩余 N×N - 已写 的格子填满（双向）。

数据格式：``(from_id, to_id, fam, trust, affection, fear, summary)``。
"""

from __future__ import annotations

from typing import TypedDict


class RelationshipPreset(TypedDict):
    from_id: str
    to_id: str
    familiarity: float
    trust: float
    affection: float
    fear: float
    summary: str


# ---------------------------------------------------------------------------
# 双向显式关系（人-人）
# 每一对都会被生成器同时写入 (a→b) 与 (b→a) 两条记录；如需不对称关系
# 请改成两条单向条目。
# ---------------------------------------------------------------------------

DEFAULT_RELATIONSHIP_PAIRS: list[tuple[str, str, float, float, float, float, str]] = [
    # —— 北区核心圈 ——
    ("npc_xiaofang", "npc_xiaowang", 0.7, 0.6, 0.4, 0.0, "熟客，每天中午给他做美式咖啡"),
    ("npc_xiaofang", "npc_xiaoming", 0.6, 0.8, 0.5, 0.0, "腼腆的常客学生"),
    ("npc_xiaofang", "npc_linna",    0.5, 0.5, 0.3, 0.0, "每天早晨来买咖啡的医生"),
    ("npc_xiaofang", "npc_chenbo",   0.7, 0.7, 0.4, 0.0, "杂货店老人家，自小看着我长大"),
    ("npc_xiaofang", "npc_ayan",     0.6, 0.6, 0.5, 0.0, "花店姑娘，常来店里点桂花拿铁"),
    ("npc_xiaoming", "npc_linna",    0.4, 0.7, 0.2, 0.0, "看过病的医生"),
    ("npc_xiaoming", "npc_chenbo",   0.4, 0.5, 0.2, 0.0, "杂货店爷爷"),
    ("npc_xiaoming", "npc_xiaoke",   0.8, 0.6, 0.5, 0.0, "高中同学，性格互补的好朋友"),
    ("npc_chenbo",   "npc_ayan",     0.5, 0.6, 0.3, 0.0, "花店姑娘，常来杂货店买猫粮"),
    ("npc_chenbo",   "npc_linna",    0.6, 0.8, 0.3, 0.0, "认识三十年的医生"),
    ("npc_xiaowang", "npc_linna",    0.4, 0.7, 0.2, 0.0, "见过几次的医生"),
    ("npc_chenbo",   "npc_meimei",   0.95, 0.95, 0.95, 0.0, "心头肉，亲孙女"),

    # —— 中区商业圈 ——
    ("npc_lihua",    "npc_xiaoyu",   0.7, 0.8, 0.6, 0.0, "新来的图书管理员，受我推荐回小镇"),
    ("npc_lihua",    "npc_xiaoming", 0.7, 0.6, 0.4, 0.1, "认真的高中生学生"),
    ("npc_lihua",    "npc_xiaoke",   0.7, 0.4, 0.3, 0.2, "调皮但聪明的中学生"),
    ("npc_lihua",    "npc_meimei",   0.6, 0.5, 0.5, 0.0, "可爱的小学生"),
    ("npc_lihua",    "npc_linna",    0.6, 0.7, 0.4, 0.0, "诊所合作医生"),
    ("npc_axin",     "npc_axu",      0.85, 0.9, 0.6, 0.0, "酒馆搭档，既是雇员也是故交"),
    ("npc_axin",     "npc_xiaodi",   0.7, 0.4, 0.4, 0.0, "酒馆常客小毛头"),
    ("npc_axin",     "npc_yueling",  0.7, 0.7, 0.5, 0.0, "经常来喝一杯的邮差好友"),
    ("npc_axin",     "npc_axiang",   0.5, 0.5, 0.3, 0.0, "沉默寡言的农场小伙"),
    ("npc_axin",     "npc_zhangdage",0.6, 0.6, 0.4, 0.0, "每晚来喝啤酒的面包师"),
    ("npc_axu",      "npc_lifang",   0.6, 0.7, 0.3, 0.0, "采购食材必找的农场主"),
    ("npc_zhangdage","npc_chenbo",   0.6, 0.6, 0.3, 0.0, "卖面粉给我的杂货店老人"),
    ("npc_zhangdage","npc_lifang",   0.6, 0.6, 0.3, 0.0, "面粉的来源"),
    ("npc_xiaoyu",   "npc_xiaoming", 0.5, 0.7, 0.3, 0.0, "常来图书馆借书的高中生"),
    ("npc_xiaoyu",   "npc_meimei",   0.6, 0.6, 0.5, 0.0, "可爱的小读者"),
    ("npc_yueling",  "npc_axiang",   0.5, 0.5, 0.5, 0.0, "默默喜欢我的小伙子（我大概知道）"),
    ("npc_yueling",  "npc_chenbo",   0.6, 0.7, 0.3, 0.0, "杂货店老主顾"),
    ("npc_yueling",  "npc_meimei",   0.7, 0.6, 0.6, 0.0, "天天追着我要糖的孩子"),

    # —— 南区农场圈 ——
    ("npc_lifang",   "npc_axiang",   0.95, 0.9, 0.85, 0.0, "亲手带大的远房侄子"),
    ("npc_lifang",   "npc_chenbo",   0.7, 0.8, 0.5, 0.0, "三十年合作的杂货店"),
    ("npc_lifang",   "npc_laomu",    0.6, 0.8, 0.4, 0.0, "镇上德高望重的老木匠"),
    ("npc_laomu",    "npc_xiaodi",   0.85, 0.8, 0.7, 0.05, "一手带大的徒弟，恨铁不成钢"),
    ("npc_laomu",    "npc_chenbo",   0.7, 0.8, 0.4, 0.0, "几十年的老朋友"),
    ("npc_laomu",    "npc_yueling",  0.5, 0.7, 0.5, 0.0, "已故学徒的妻子，照看着她"),

    # —— 跨区社交（咖啡店、广场是社交锚点）——
    ("npc_xiaofang", "npc_yueling",  0.5, 0.5, 0.3, 0.0, "送信路过爱聊几句的邮差"),
    ("npc_xiaofang", "npc_xiaoyu",   0.5, 0.6, 0.3, 0.0, "新来的图书管理员，常来"),
    ("npc_xiaofang", "npc_lihua",    0.5, 0.6, 0.3, 0.0, "校长偶尔早上来"),
    ("npc_xiaofang", "npc_zhangdage",0.6, 0.6, 0.4, 0.0, "面包供应商，互相照应"),
    ("npc_ayan",     "npc_xiaoyu",   0.5, 0.6, 0.5, 0.0, "都内向，惺惺相惜"),
    ("npc_ayan",     "npc_meimei",   0.7, 0.6, 0.7, 0.0, "总来花店逗猫的小可爱"),
    ("npc_linna",    "npc_lihua",    0.6, 0.7, 0.4, 0.0, "学校驻校医生，互相扶持"),
    ("npc_linna",    "npc_lifang",   0.5, 0.6, 0.2, 0.0, "时常给农场工人看病"),
    ("npc_xiaowang", "npc_xiaoyu",   0.4, 0.6, 0.3, 0.0, "图书馆借书认识的"),
    ("npc_xiaoke",   "npc_meimei",   0.7, 0.5, 0.5, 0.0, "邻居家的小妹妹"),
    ("npc_xiaodi",   "npc_axiang",   0.5, 0.6, 0.3, 0.0, "都是镇里出来的，常一起喝酒"),
    ("npc_zhangdage","npc_meimei",   0.7, 0.6, 0.6, 0.0, "天天来店里要可颂的小天使"),
]


# ---------------------------------------------------------------------------
# 主人 / 宠物双向关系（一条单向定义同时生成两条记录）
# ---------------------------------------------------------------------------

DEFAULT_OWNER_PAIRS: list[tuple[str, str, str]] = [
    # (animal_id, owner_id, summary_for_animal_to_owner)
    ("animal_doudou",  "npc_xiaofang", "主人"),
    ("animal_heihei",  "npc_chenbo",   "主人"),
    ("animal_mimi",    "npc_ayan",     "主人"),
    ("animal_xiaobai", "npc_ayan",     "最近收养我的人"),
]
