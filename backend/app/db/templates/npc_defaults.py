"""默认 22 NPC 模板（18 人类 + 4 动物）+ 默认玩家。

该模板既是 ``world_gen`` 在新游戏初始化时的种子数据，也是前端
``GET /api/games/templates/npcs`` 返回的"默认人口"列表（用户可在
NPC 编辑器里增删改）。

设计要点：
- 每个 NPC 都给定 ``preferred_district`` / ``home_role`` 等元数据，
  ``world_gen.placement`` 会据此把住宅自动放进合适的区域。
- ``schedule_template`` 仅记录 ``schedule_id``（指向 ``SCHEDULE_TEMPLATES``）；
  生成器在分配完场景 / 工作地后通过 ``resolve_schedule`` 替换占位符。
- ``art.layers`` 列出 LPC 层组合，与 ``scripts/compose_lpc.py`` 中 NPC_LAYERS 完全对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class AgentTemplate:
    """与 ``agents`` 表 + 美术档案对齐的纯数据结构。

    所有字段都可被前端 NPC 编辑器读出 / 修改。
    """

    id: str
    name: str
    entity_type: Literal["human", "animal", "player"]
    age: int | None = None
    gender: Literal["male", "female"] | None = None
    species: str | None = None
    occupation: str | None = None
    personality: list[str] = field(default_factory=list)
    background: str = ""
    lifestyle: str | None = None
    long_term_goals: list[str] = field(default_factory=list)
    schedule_id: str | None = None  # 指向 SCHEDULE_TEMPLATES 的某个 key

    # 美术档案 —— 人类用 LPC 各层 ID（参考 LPC_LAYER_CATALOG），动物用颜色
    art: dict[str, Any] = field(default_factory=dict)
    accent_color: str = "#cccccc"
    portrait_url: str | None = None

    # 居住安排
    has_home: bool = True
    upstairs_of: str | None = None  # has_home=False 时，住在某公共建筑楼上
    cohabits_with: str | None = None  # 与某 NPC 共住（不分配独立住宅）

    # 区域偏好（北/中/南），world_gen 据此分配住宅位置
    preferred_district: Literal["north", "center", "south"] = "north"

    # 动物专用：主人 NPC ID
    owner_id: str | None = None

    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


# ---------------------------------------------------------------------------
# 18 个默认人类 NPC
# ---------------------------------------------------------------------------

DEFAULT_HUMAN_TEMPLATES: list[AgentTemplate] = [
    # ───── 北区 6 人（旧版兼容） ─────
    AgentTemplate(
        id="npc_xiaofang",
        name="小芳",
        entity_type="human",
        age=24, gender="female",
        occupation="咖啡店店员",
        personality=["热情", "细心", "外向"],
        background="小芳在 Hobbs 咖啡店当店员两年了，记得每一位常客的口味；从小养着金毛犬豆豆。",
        lifestyle="早起锻炼，白天在咖啡店工作，晚上读诗。",
        long_term_goals=["成为咖啡师", "让小镇每个人都喝到一杯属于自己的咖啡"],
        schedule_id="cafe_staff",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaofang", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "ponytail_raven", "torso": "apron_fem_white",
            "legs": "pants_fem_black", "feet": "shoes_fem_brown",
        }},
        accent_color="#ffadc2",
        portrait_url="/assets/portraits/humans/xiaofang.png",
        preferred_district="north",
    ),
    AgentTemplate(
        id="npc_xiaoming",
        name="小明",
        entity_type="human",
        age=16, gender="male",
        occupation="高中生",
        personality=["腼腆", "认真", "好奇"],
        background="小镇学校的高中生，喜欢放学后去咖啡店做作业；和小芳熟络但容易紧张。",
        lifestyle="规律上学，喜欢读书，对世界保有好奇心。",
        long_term_goals=["考上城里的好大学", "克服害羞主动和人交流"],
        schedule_id="high_school_student",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaoming", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_black", "torso": "ls_male_blue",
            "legs": "pants_male_black", "feet": "shoes_male_brown",
        }},
        accent_color="#8cc0ff",
        portrait_url="/assets/portraits/humans/xiaoming.png",
        preferred_district="north",
    ),
    AgentTemplate(
        id="npc_xiaowang",
        name="小王",
        entity_type="human",
        age=28, gender="male",
        occupation="程序员",
        personality=["理性", "内向", "喜欢咖啡"],
        background="远程办公的程序员，每天中午来咖啡店点一杯美式（绝对不加糖）；和小芳的默契建立在沉默的咖啡上。",
        lifestyle="远程办公，规律吃饭，一周锻炼三次。",
        long_term_goals=["写一个属于自己的开源项目", "试着多和邻里打招呼"],
        schedule_id="remote_worker",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaowang", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_brown", "torso": "ls_male_charcoal",
            "legs": "pants_male_charcoal", "feet": "shoes_male_brown",
        }},
        accent_color="#8cffd1",
        portrait_url="/assets/portraits/humans/xiaowang.png",
        preferred_district="north",
    ),
    AgentTemplate(
        id="npc_linna",
        name="林娜",
        entity_type="human",
        age=34, gender="female",
        occupation="医生",
        personality=["冷静", "负责", "专业"],
        background="小镇全科医生，借用学校的一间教室当临时诊所；熟悉每位居民的健康。",
        lifestyle="规律作息，饭后徒步，一年带头组织两次免费义诊。",
        long_term_goals=["在小镇建一座真正的诊所", "把急救知识普及到每户人家"],
        schedule_id="clinic",
        art={"kind": "lpc_human", "sheet_id": "npc_linna", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "long_black", "torso": "ls_fem_bluegray",
            "legs": "pants_fem_black", "feet": "shoes_fem_brown",
        }},
        accent_color="#fff0a1",
        portrait_url="/assets/portraits/humans/linna.png",
        preferred_district="north",
    ),
    AgentTemplate(
        id="npc_chenbo",
        name="陈伯",
        entity_type="human",
        age=58, gender="male",
        occupation="杂货店老板",
        personality=["和善", "健谈", "怀旧"],
        background="守着杂货店三十年的老板，是小镇的活地图；与孙女美美同住，养着看门犬黑黑。",
        lifestyle="天亮就开门，晚上去河边乘凉。",
        long_term_goals=["把杂货店传给值得托付的年轻人", "陪美美健康长大"],
        schedule_id="shopkeeper",
        art={"kind": "lpc_human", "sheet_id": "npc_chenbo", "layers": {
            "body": "male_taupe", "head": "male_taupe",
            "hair": "short_male_gray", "torso": "ls_male_gray",
            "legs": "pants_male_brown", "feet": "shoes_male_brown",
        }},
        accent_color="#d9a066",
        portrait_url="/assets/portraits/humans/chenbo.png",
        preferred_district="north",
    ),
    AgentTemplate(
        id="npc_ayan",
        name="阿言",
        entity_type="human",
        age=26, gender="female",
        occupation="花店店主",
        personality=["敏感", "艺术化", "爱猫"],
        background="经营一家小小的花店，养了两只猫咪（咪咪、小白）；性格内敛但对花和动物很温柔。",
        lifestyle="喜欢在花园里发呆，黄昏会去河边写生。",
        long_term_goals=["让花店成为小镇最美的角落", "出版一本花艺笔记"],
        schedule_id="florist",
        art={"kind": "lpc_human", "sheet_id": "npc_ayan", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "long_lavender", "torso": "ls_fem_lavender",
            "legs": "pants_fem_brown", "feet": "shoes_fem_brown",
        }},
        accent_color="#c8a2ff",
        portrait_url="/assets/portraits/humans/ayan.png",
        preferred_district="north",
    ),

    # ───── 中区 6 人 ─────
    AgentTemplate(
        id="npc_lihua",
        name="李华",
        entity_type="human",
        age=45, gender="female",
        occupation="校长",
        personality=["严厉", "博学", "有威信"],
        background="小镇学校校长兼语文老师；本地长大，三十岁后接管学校；对每个学生的家底如数家珍。",
        lifestyle="读书写字，晚饭后去图书馆备课。",
        long_term_goals=["让小镇每个孩子都能受良好教育", "把学校办成图书馆和社区中心一体的场所"],
        schedule_id="principal",
        art={"kind": "lpc_human", "sheet_id": "npc_lihua", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "long_brown", "torso": "ls_fem_red",
            "legs": "pants_fem_black", "feet": "shoes_fem_brown",
        }},
        accent_color="#d56b6b",
        preferred_district="center",
    ),
    AgentTemplate(
        id="npc_zhangdage",
        name="张大哥",
        entity_type="human",
        age=38, gender="male",
        occupation="面包师",
        personality=["朴实", "勤劳", "嗓门大"],
        background="自小学厨，开了一家小面包店；凌晨四点起床烤面包，下午常和小镇老兵打牌。",
        lifestyle="早睡早起；下午打盹一小时再开炉。",
        long_term_goals=["把面包店扩成小镇早餐总店", "让陈伯的孙女美美长大也爱吃他做的可颂"],
        schedule_id="baker",
        has_home=False,
        upstairs_of="bakery",
        art={"kind": "lpc_human", "sheet_id": "npc_zhangdage", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_black", "torso": "apron_male_white",
            "legs": "pants_male_brown", "feet": "shoes_male_brown",
        }},
        accent_color="#e8c4a0",
        preferred_district="north",
        notes="住面包店楼上",
    ),
    AgentTemplate(
        id="npc_xiaoyu",
        name="小雨",
        entity_type="human",
        age=22, gender="female",
        occupation="图书管理员",
        personality=["安静", "爱读书", "好奇"],
        background="刚从城里大学毕业回到小镇，接手了图书馆；总在角落里读书；喜欢观察邻居。",
        lifestyle="安静作息，午饭去咖啡店写日记。",
        long_term_goals=["让图书馆藏书翻倍", "组织一个读书会"],
        schedule_id="librarian",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaoyu", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "short_fem_black", "torso": "ls_fem_lavender",
            "legs": "pants_fem_blue", "feet": "shoes_fem_brown",
        }},
        accent_color="#a4c8ff",
        preferred_district="center",
    ),
    AgentTemplate(
        id="npc_axin",
        name="阿欣",
        entity_type="human",
        age=30, gender="female",
        occupation="酒馆老板娘",
        personality=["豪爽", "世故", "嘴硬心软"],
        background="开了一家温暖的酒馆，是小镇所有秘密的容器；离过一次婚，对感情看得开。",
        lifestyle="夜猫子，下午两点起床；爱听人讲故事。",
        long_term_goals=["让酒馆成为镇上每个孤独人的归属", "存钱给酒馆装个壁炉"],
        schedule_id="tavern_owner",
        has_home=False,
        upstairs_of="tavern",
        art={"kind": "lpc_human", "sheet_id": "npc_axin", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "long_brown", "torso": "ls_fem_red",
            "legs": "pants_fem_brown", "feet": "shoes_fem_brown",
        }},
        accent_color="#ff8888",
        preferred_district="center",
        notes="住酒馆楼上",
    ),
    AgentTemplate(
        id="npc_axu",
        name="阿旭",
        entity_type="human",
        age=27, gender="male",
        occupation="厨师",
        personality=["严谨", "骄傲", "刀工很好"],
        background="酒馆厨师，曾在城里大酒店工作；对食材近乎挑剔；和阿欣是故交，回小镇主厨。",
        lifestyle="跟阿欣一样夜猫子；早上采买，下午午休。",
        long_term_goals=["让酒馆的菜单上有小镇人记得一辈子的一道菜"],
        schedule_id="chef",
        has_home=False,
        upstairs_of="tavern",
        art={"kind": "lpc_human", "sheet_id": "npc_axu", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_black", "torso": "apron_male_white",
            "legs": "pants_male_charcoal", "feet": "shoes_male_brown",
        }},
        accent_color="#dde6ff",
        preferred_district="center",
        notes="住酒馆楼上的另一间",
    ),
    AgentTemplate(
        id="npc_yueling",
        name="月玲",
        entity_type="human",
        age=32, gender="female",
        occupation="邮差",
        personality=["开朗", "健谈", "认得每条小路"],
        background="负责小镇所有邮件投递；嫁给了已故木匠，独自抚养孩子；性格阳光。",
        lifestyle="八点出门巡街，晚上常和阿欣喝一杯。",
        long_term_goals=["让镇上的每封信都安全送到", "再去远方旅行一次"],
        schedule_id="postal_worker",
        art={"kind": "lpc_human", "sheet_id": "npc_yueling", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "short_fem_brown", "torso": "ls_fem_bluegray",
            "legs": "pants_fem_blue", "feet": "shoes_fem_brown",
        }},
        accent_color="#ffd97a",
        preferred_district="center",
    ),

    # ───── 南区 5 人 ─────
    AgentTemplate(
        id="npc_laomu",
        name="老穆",
        entity_type="human",
        age=62, gender="male",
        occupation="退休木匠",
        personality=["慈祥", "念旧", "手艺人"],
        background="小镇最受敬重的老木匠；年轻时为半个镇子打过家具；近年带着学徒小迪。",
        lifestyle="清晨在河边遛弯，下午在木工坊指点江山。",
        long_term_goals=["把毕生手艺传给小迪", "为孙辈做一套家传木匣"],
        schedule_id="elder_craftsman",
        art={"kind": "lpc_human", "sheet_id": "npc_laomu", "layers": {
            "body": "male_taupe", "head": "male_taupe",
            "hair": "short_male_gray", "torso": "ls_male_brown",
            "legs": "pants_male_brown", "feet": "shoes_male_brown",
        }},
        accent_color="#a47a55",
        preferred_district="south",
    ),
    AgentTemplate(
        id="npc_xiaodi",
        name="小迪",
        entity_type="human",
        age=19, gender="male",
        occupation="学徒木匠",
        personality=["积极", "莽撞", "嘴贫"],
        background="孤儿院出身，被老穆收留学木工；急于证明自己，常因毛躁出错；爱去酒馆。",
        lifestyle="天没亮就起床，晚上必去酒馆。",
        long_term_goals=["三年内独立接活", "买套自己的家具"],
        schedule_id="apprentice",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaodi", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_brown", "torso": "ls_male_blue",
            "legs": "pants_male_charcoal", "feet": "shoes_male_brown",
        }},
        accent_color="#9ad5ff",
        preferred_district="south",
    ),
    AgentTemplate(
        id="npc_lifang",
        name="李芳",
        entity_type="human",
        age=50, gender="female",
        occupation="农场主",
        personality=["务实", "朴素", "话少但有主见"],
        background="经营小镇南郊的农场二十年，给镇上提供蔬果；是镇上几乎所有人的食物来源。",
        lifestyle="天亮下田，晚上必看一会儿月亮才睡。",
        long_term_goals=["让农场再扩大一倍", "教阿祥独立打理一片田"],
        schedule_id="farmer_owner",
        art={"kind": "lpc_human", "sheet_id": "npc_lifang", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "long_brown", "torso": "ls_fem_bluegray",
            "legs": "pants_fem_brown", "feet": "shoes_fem_brown",
        }},
        accent_color="#9bc77a",
        preferred_district="south",
    ),
    AgentTemplate(
        id="npc_axiang",
        name="阿祥",
        entity_type="human",
        age=24, gender="male",
        occupation="农场工人",
        personality=["沉默", "勤恳", "肚里有数"],
        background="李芳的远房侄子，从小跟着她长大；不擅言辞但什么都懂；每次问什么先想十秒再答。",
        lifestyle="天亮就下田，傍晚去酒馆喝一杯解乏。",
        long_term_goals=["独立打理一块田", "鼓起勇气向月玲表白"],
        schedule_id="farmer_worker",
        art={"kind": "lpc_human", "sheet_id": "npc_axiang", "layers": {
            "body": "male_taupe", "head": "male_taupe",
            "hair": "short_male_brown", "torso": "ls_male_brown",
            "legs": "pants_male_brown", "feet": "shoes_male_brown",
        }},
        accent_color="#c2a37e",
        preferred_district="south",
        notes="与李芳同住农场屋",
        cohabits_with="npc_lifang",
    ),
    AgentTemplate(
        id="npc_meimei",
        name="美美",
        entity_type="human",
        age=7, gender="female",
        occupation="小学生",
        personality=["天真", "爱粘人", "话密"],
        background="陈伯的孙女，父母在外地；和爷爷一起住在杂货店楼上；是小镇所有人的宠儿。",
        lifestyle="放学后在小镇到处跑，最爱去花店逗猫。",
        long_term_goals=["把镇上每只猫狗都摸过一遍", "学会独立上学"],
        schedule_id="primary_school_student",
        cohabits_with="npc_chenbo",
        art={"kind": "lpc_human", "sheet_id": "npc_meimei", "layers": {
            "body": "female_light", "head": "female_light",
            "hair": "short_fem_brown", "torso": "ls_fem_lavender",
            "legs": "pants_fem_blue", "feet": "shoes_fem_brown",
        }},
        accent_color="#ffc7b1",
        preferred_district="north",
        notes="与陈伯同住",
    ),
    AgentTemplate(
        id="npc_xiaoke",
        name="小柯",
        entity_type="human",
        age=14, gender="male",
        occupation="中学生",
        personality=["调皮", "聪明", "爱出风头"],
        background="小明的同学，性格与小明互补；爱在广场打闹；偷偷暗恋小芳。",
        lifestyle="放学后必去公园；周末跑农场偷果子。",
        long_term_goals=["在镇运动会拿奖", "让心仪的人对自己刮目相看"],
        schedule_id="middle_school_student",
        art={"kind": "lpc_human", "sheet_id": "npc_xiaoke", "layers": {
            "body": "male_light", "head": "male_light",
            "hair": "short_male_brown", "torso": "ls_male_red",
            "legs": "pants_male_blue", "feet": "shoes_male_brown",
        }},
        accent_color="#ff9c5a",
        preferred_district="north",
    ),
]


# ---------------------------------------------------------------------------
# 4 个默认动物
# ---------------------------------------------------------------------------

DEFAULT_ANIMAL_TEMPLATES: list[AgentTemplate] = [
    AgentTemplate(
        id="animal_doudou",
        name="豆豆",
        entity_type="animal",
        species="dog",
        personality=["亲人", "活泼", "friendly"],
        background="小芳从小养大的金毛犬，对所有人都摇尾巴。",
        owner_id="npc_xiaofang",
        art={"kind": "lpc_animal", "species": "dog", "color": "golden", "sheet_id": "lpc_dogs"},
        accent_color="#f5c06e",
        portrait_url="/assets/portraits/animals/doudou.png",
        has_home=False,
        cohabits_with="npc_xiaofang",
        preferred_district="north",
    ),
    AgentTemplate(
        id="animal_heihei",
        name="黑黑",
        entity_type="animal",
        species="dog",
        personality=["警觉", "护家", "guard"],
        background="陈伯收养的中华田园犬，对陌生人保持距离。",
        owner_id="npc_chenbo",
        art={"kind": "lpc_animal", "species": "dog", "color": "black", "sheet_id": "lpc_dogs"},
        accent_color="#3a3a3a",
        portrait_url="/assets/portraits/animals/heihei.png",
        has_home=False,
        cohabits_with="npc_chenbo",
        preferred_district="north",
    ),
    AgentTemplate(
        id="animal_mimi",
        name="咪咪",
        entity_type="animal",
        species="cat",
        personality=["独立", "爱晒太阳"],
        background="独立的三花猫，只在阳光好的时候出现。",
        owner_id="npc_ayan",
        art={"kind": "lpc_animal", "species": "cat", "color": "brown", "sheet_id": "lpc_cats"},
        accent_color="#ffb4a8",
        portrait_url="/assets/portraits/animals/mimi.png",
        has_home=False,
        cohabits_with="npc_ayan",
        preferred_district="north",
    ),
    AgentTemplate(
        id="animal_xiaobai",
        name="小白",
        entity_type="animal",
        species="cat",
        personality=["胆小", "好奇", "shy", "fearful"],
        background="阿言新收留的小白猫，怕陌生人但黏阿言。",
        owner_id="npc_ayan",
        art={"kind": "lpc_animal", "species": "cat", "color": "white", "sheet_id": "lpc_cats"},
        accent_color="#f0f0f0",
        portrait_url="/assets/portraits/animals/xiaobai.png",
        has_home=False,
        cohabits_with="npc_ayan",
        preferred_district="north",
    ),
]


# ---------------------------------------------------------------------------
# 默认玩家
# ---------------------------------------------------------------------------

DEFAULT_PLAYER_TEMPLATE = AgentTemplate(
    id="player_default",
    name="旅人",
    entity_type="player",
    age=None, gender=None,
    occupation=None,
    personality=["好奇", "友善"],
    background="初到小镇的访客，对小镇的一切都充满好奇。",
    lifestyle="无固定作息，按自己心意行动。",
    long_term_goals=["认识小镇里的每个人"],
    schedule_id=None,
    art={"kind": "lpc_human", "sheet_id": "player_default", "layers": {
        "body": "male_light", "head": "male_light",
        "hair": "short_male_brown", "torso": "ls_male_blue",
        "legs": "pants_male_brown", "feet": "shoes_male_brown",
    }},
    accent_color="#ffffff",
    portrait_url="/assets/portraits/humans/player_default.png",
    has_home=False,
)
