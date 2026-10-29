"""种子数据：科室映射、演示医生、红旗规则、知识库、预设角色。

演示环境的医生标 is_mock（PRD §11 的演示环境开关）。真实签约后从后台导入，
不清空这里的种子。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .database import now
from .modules.safety import REDFLAG_SEED

# code, name, 开放咨询, 描述
DEPARTMENTS: list[tuple[str, str, bool, str]] = [
    ("cardiology", "心血管内科", True, "胸闷、心悸、血压相关"),
    ("neurology", "神经内科", True, "头晕头痛、失眠、肢体感觉异常"),
    ("endocrinology", "内分泌科", True, "血糖甲状腺、体重变化"),
    ("dermatology", "皮肤科", True, "皮疹、瘙痒、皮肤肿物"),
    ("gastroenterology", "消化内科", False, "腹痛、反酸、排便异常"),
    ("respiratory", "呼吸内科", False, "咳嗽咳痰、气喘"),
    ("orthopedics", "骨科", False, "关节疼痛、腰背痛、外伤"),
    ("gynecology", "妇科", False, "月经异常、妇科不适"),
    ("pediatrics", "儿科", False, "儿童常见症状"),
    ("ophthalmology", "眼科", False, "视力变化、眼干眼痛"),
    ("ent", "耳鼻喉科", False, "咽痛、鼻塞、听力变化"),
    ("psychiatry", "精神心理科", False, "情绪困扰、睡眠问题、焦虑"),
]

# symptom → department
SYMPTOM_MAP: list[tuple[str, str, float]] = [
    ("头晕", "neurology", 2.0), ("头痛", "neurology", 2.0), ("失眠", "neurology", 1.5),
    ("肢体麻木", "neurology", 2.0), ("记忆力下降", "neurology", 1.0),
    ("胸痛", "cardiology", 2.5), ("胸闷", "cardiology", 2.5), ("心悸", "cardiology", 2.5),
    ("心慌", "cardiology", 2.0), ("血压高", "cardiology", 2.0), ("高血压", "cardiology", 2.0),
    ("血糖", "endocrinology", 2.5), ("糖尿病", "endocrinology", 2.5), ("甲状腺", "endocrinology", 2.5),
    ("口渴", "endocrinology", 1.5), ("体重下降", "endocrinology", 1.5), ("脖子粗", "endocrinology", 1.5),
    ("皮疹", "dermatology", 2.5), ("瘙痒", "dermatology", 2.5), ("皮肤", "dermatology", 2.0),
    ("痘痘", "dermatology", 1.5), ("脱发", "dermatology", 1.0),
    ("胃痛", "gastroenterology", 2.0), ("反酸", "gastroenterology", 2.0), ("腹泻", "gastroenterology", 2.0),
    ("便秘", "gastroenterology", 1.5), ("恶心", "gastroenterology", 1.5),
    ("咳嗽", "respiratory", 2.0), ("咳痰", "respiratory", 2.0), ("气喘", "respiratory", 2.0),
    ("嗓子疼", "ent", 2.0), ("咽痛", "ent", 2.0), ("鼻塞", "ent", 2.0), ("打鼾", "ent", 1.5),
    ("腰疼", "orthopedics", 2.0), ("关节痛", "orthopedics", 2.0), ("颈椎", "orthopedics", 1.5),
    ("眼睛干", "ophthalmology", 2.0), ("视力下降", "ophthalmology", 2.0),
    ("月经", "gynecology", 2.5), ("痛经", "gynecology", 2.5),
    ("孩子发烧", "pediatrics", 2.0), ("小孩", "pediatrics", 1.5),
    ("焦虑", "psychiatry", 2.0), ("情绪低落", "psychiatry", 2.0), ("压力大", "psychiatry", 1.5),
]

# name, dept, title, hospital, specialties, price_cents, reply_hours, rating, reviews, online
DOCTORS: list[tuple] = [
    ("张明远", "neurology", "主任医师", "市第一人民医院", ["眩晕", "脑血管病"], 9900, 2.1, 4.9, 42, "online"),
    ("李慧珍", "neurology", "副主任医师", "市中心医院", ["头痛", "睡眠障碍"], 6900, 3.4, 4.7, 28, "online"),
    ("王建国", "neurology", "主治医师", "区人民医院", ["头晕", "颈椎病"], 3900, 5.2, 4.5, 12, "busy"),
    ("陈立群", "cardiology", "主任医师", "省人民医院", ["冠心病", "高血压"], 12900, 1.8, 4.8, 55, "online"),
    ("赵淑芬", "cardiology", "副主任医师", "市第一人民医院", ["心衰", "心律失常"], 8900, 2.9, 4.6, 31, "online"),
    ("刘志强", "cardiology", "主治医师", "区人民医院", ["高血压管理"], 4900, 6.0, 4.4, 9, "offline"),
    ("周雅琴", "endocrinology", "主任医师", "省人民医院", ["糖尿病", "甲状腺疾病"], 10900, 2.4, 4.9, 38, "online"),
    ("孙文博", "endocrinology", "副主任医师", "市中心医院", ["糖尿病教育", "肥胖"], 7900, 4.1, 4.6, 22, "busy"),
    ("吴桂芳", "dermatology", "主任医师", "市皮肤病医院", ["湿疹", "银屑病"], 8900, 3.0, 4.8, 47, "online"),
    ("郑晓东", "dermatology", "主治医师", "区人民医院", ["痤疮", "过敏性皮炎"], 4500, 5.5, 4.5, 15, "online"),
    # —— 以下覆盖剩余 8 个科室，保证演示时任意科室都有医生可看 ——
    ("何守仁", "gastroenterology", "主任医师", "省人民医院", ["胃食管反流", "慢性胃炎"], 9900, 2.6, 4.8, 36, "online"),
    ("许敏", "gastroenterology", "副主任医师", "市中心医院", ["肠易激综合征", "幽门螺杆菌"], 6900, 3.8, 4.6, 24, "online"),
    ("马国栋", "respiratory", "主任医师", "市第一人民医院", ["慢阻肺", "哮喘"], 9400, 2.2, 4.7, 41, "online"),
    ("杨帆", "respiratory", "主治医师", "区人民医院", ["慢性咳嗽", "肺部感染"], 4200, 5.0, 4.4, 18, "busy"),
    ("邓志刚", "orthopedics", "主任医师", "省骨科医院", ["腰椎间盘突出", "膝关节退变"], 11900, 2.8, 4.9, 52, "online"),
    ("罗海燕", "orthopedics", "副主任医师", "市中心医院", ["颈椎病", "运动损伤"], 7400, 4.2, 4.5, 27, "online"),
    ("唐丽娟", "gynecology", "主任医师", "市妇幼保健院", ["月经失调", "多囊卵巢"], 10400, 2.5, 4.8, 44, "online"),
    ("冯雪梅", "gynecology", "主治医师", "区妇幼保健院", ["孕期保健", "妇科炎症"], 4900, 4.8, 4.6, 20, "online"),
    ("程晓阳", "pediatrics", "主任医师", "市儿童医院", ["小儿呼吸道感染", "生长发育"], 8900, 2.0, 4.9, 61, "online"),
    ("严芳", "pediatrics", "副主任医师", "区人民医院", ["小儿腹泻", "喂养指导"], 5900, 3.5, 4.7, 33, "online"),
    ("卢明哲", "ophthalmology", "主任医师", "市眼科医院", ["干眼症", "白内障"], 8600, 2.7, 4.8, 29, "online"),
    ("于慧", "ophthalmology", "主治医师", "区人民医院", ["屈光不正", "结膜炎"], 4100, 5.3, 4.4, 14, "busy"),
    ("邵文远", "ent", "主任医师", "市第一人民医院", ["慢性咽炎", "鼻窦炎"], 8100, 2.9, 4.7, 38, "online"),
    ("汪晓琳", "ent", "副主任医师", "市中心医院", ["耳鸣", "过敏性鼻炎"], 6200, 3.7, 4.6, 26, "online"),
    ("傅安宁", "psychiatry", "主任医师", "市精神卫生中心", ["焦虑障碍", "睡眠障碍"], 10800, 2.3, 4.9, 49, "online"),
    ("柳青", "psychiatry", "心理治疗师", "市心理咨询中心", ["情绪调节", "压力管理"], 5600, 4.5, 4.7, 35, "online"),
]

# title, aliases, dept, summary, content, seek_care_signs, source, url
KB_ENTRIES: list[tuple] = [
    (
        "体位性低血压",
        ["起身头晕", "站起来头晕", "体位改变头晕"],
        "neurology",
        "从卧位或坐位突然站起时血压来不及调节，出现短暂头晕、眼前发黑，通常几秒到几分钟自行缓解。",
        "体位性低血压是头晕的常见原因之一。人体从平躺变为直立时，约有 300~800ml 血液因重力淤积在下肢，"
        "健康人通过血管收缩与心率加快在数秒内代偿；代偿变慢时脑供血短暂下降，就出现头晕、眼前发黑。"
        "常见诱因包括起身过快、长时间卧床、脱水、某些降压药、餐后血液集中于胃肠道、热水澡后血管扩张。"
        "日常可以这样做：改变体位时放慢速度，先坐起 30 秒再站；保证饮水；避免饱餐后立即起身。",
        ["伴随胸痛或心悸", "反复晕倒或意识丧失", "单侧肢体无力或言语不清", "休息状态下也持续头晕"],
        "明路医学顾问组",
        "https://example.cn/health/orthostatic-hypotension",
    ),
    (
        "家庭自测血压的正确方法",
        ["量血压", "测血压", "血压怎么量"],
        "cardiology",
        "家庭自测血压比诊室单次测量更能反映日常水平，但测量方法不对会让数值偏差 10mmHg 以上。",
        "测量前 30 分钟不吸烟、不喝咖啡、排空膀胱，安静坐 5 分钟。坐姿靠背，双脚平放不交叉，"
        "手臂放在桌上与心脏同高，袖带下缘距肘窝 2~3 厘米，松紧以能插入一指为宜。"
        "测量时不说话。建议每天早晚各测一次，每次连续测 2 遍取平均，间隔 1 分钟。"
        "家用上臂式电子血压计需定期校准。单次数值意义有限，连续记录 7 天的趋势更有参考价值。",
        ["收缩压持续 ≥180 或舒张压 ≥110", "伴胸痛、呼吸困难、剧烈头痛", "伴视物模糊或肢体无力"],
        "明路医学顾问组",
        "https://example.cn/health/home-bp",
    ),
    (
        "2 型糖尿病的血糖监测",
        ["血糖监测", "测血糖", "糖尿病检查"],
        "endocrinology",
        "血糖监测不只是看空腹值，还要看餐后与波动，单次数值无法判断控制情况。",
        "常用监测点包括空腹、餐后 2 小时、睡前。糖化血红蛋白（HbA1c）反映近 2~3 个月的平均血糖水平，"
        "建议每 3 个月复查一次。自我监测记录应包含时间、数值、当时的用药与饮食，"
        "复诊时带给医生看。血糖偶尔一次偏高不必紧张，持续趋势异常才需要调整方案，"
        "且调整用药必须由医生决定。",
        ["血糖 <3.9mmol/L 伴出汗心慌", "反复恶心呕吐、呼气有烂苹果味", "意识模糊或嗜睡"],
        "明路医学顾问组",
        "https://example.cn/health/glucose-monitoring",
    ),
    (
        "接触性皮炎的护理",
        ["皮肤过敏", "皮炎", "皮肤痒"],
        "dermatology",
        "接触性皮炎是皮肤接触刺激物或过敏原后出现的红斑、丘疹、瘙痒，处理关键是先找并避开原因。",
        "急性期用清水轻洗，避免热水、肥皂、酒精刺激，不要抓挠。可以冷敷缓解瘙痒。"
        "记录发病前 24 小时接触过的物品（新洗衣液、首饰、植物、外用药）有助于找原因。"
        "衣物选纯棉宽松，保持患处干燥。多数轻症在避开原因后 1~2 周逐渐好转。",
        ["面部或眼睑明显肿胀", "出现水疱糜烂或渗液", "伴发热", "皮疹迅速扩散至全身"],
        "明路医学顾问组",
        "https://example.cn/health/contact-dermatitis",
    ),
    (
        "失眠的非药物调整",
        ["睡不着", "失眠", "睡眠不好"],
        "neurology",
        "短期失眠多数与作息和睡前习惯有关，先调整行为通常比用药更安全。",
        "固定起床时间（包括周末）比固定入睡时间更有效。床只用来睡觉，不要在床上工作或刷手机。"
        "午后避免咖啡因，睡前 3 小时不剧烈运动、不过饱。卧室保持暗、静、凉。"
        "躺下 20 分钟仍清醒就起床，做点单调的事，有困意再回床。白天接触自然光有助于夜间褪黑素分泌。",
        ["持续 3 个月以上每周 ≥3 晚失眠", "白天严重嗜睡影响工作", "伴明显情绪低落或消极念头"],
        "明路医学顾问组",
        "https://example.cn/health/insomnia",
    ),
    (
        "发热的家庭观察要点",
        ["发烧", "体温高", "退烧"],
        "respiratory",
        "发热是症状不是疾病。多数成人发热由病毒感染引起，3~5 天自限，关键是观察危险信号而不是紧盯温度。",
        "38.5℃ 以下、精神状态好，可以多喝水、休息，不必急着用退烧药。退烧药的作用是缓解不适，"
        "不缩短病程，也不治疗病因。成人常用对乙酰氨基酚或布洛芬，二选一，不要交替或叠加使用，"
        "间隔至少 4~6 小时，24 小时内不超过规定次数。物理降温以温水擦拭为主，禁用酒精擦浴。"
        "记录体温变化曲线比单次数值有用。持续发热超过 3 天、或退烧后精神仍然很差，需要就诊。",
        ["体温 ≥39.5℃ 且退烧药无效", "意识模糊、抽搐、颈项强直", "呼吸困难或口唇发紫", "3 个月以下婴儿发热"],
        "明路医学顾问组",
        "https://example.cn/health/fever",
    ),
    (
        "慢性咳嗽要找原因",
        ["咳嗽老不好", "咳了一个月", "干咳"],
        "respiratory",
        "咳嗽超过 8 周属于慢性咳嗽，常见原因有鼻后滴漏、咳嗽变异性哮喘、胃食管反流三类，单纯止咳往往无效。",
        "先从时间线入手：夜间或晨起加重、伴鼻塞流涕，多见于上气道问题；遇冷空气、运动后加重，"
        "要考虑咳嗽变异性哮喘；餐后或平卧时明显、伴反酸，可能是反流性咳嗽。"
        "吸烟史、职业粉尘接触史也很关键。慢性咳嗽不建议长期自行服用强力镇咳药，"
        "它会掩盖症状并影响排痰。多数情况需要胸片或肺功能检查来区分原因。",
        ["咯血", "不明原因消瘦", "进行性呼吸困难", "长期吸烟且咳嗽性质改变"],
        "明路医学顾问组",
        "https://example.cn/health/chronic-cough",
    ),
    (
        "胃食管反流的日常管理",
        ["反酸", "烧心", "胃酸"],
        "gastroenterology",
        "烧心与反酸是胃内容物反流到食管引起的，生活方式调整能解决相当一部分轻症。",
        "进食后 2~3 小时内不要平卧，睡前 3 小时不进食。抬高床头 15~20 厘米（垫高床脚而非加枕头）。"
        "减少高脂、辛辣、巧克力、浓茶咖啡与碳酸饮料；戒烟限酒。少量多餐，避免一次吃太饱。"
        "超重者减重效果明显，腰带不要过紧。症状偶发可按需用抑酸药，但连续使用超过 2 周需就医评估。",
        ["吞咽困难或吞咽疼痛", "呕血或黑便", "不明原因体重下降", "胸痛伴出汗需先排除心脏问题"],
        "明路医学顾问组",
        "https://example.cn/health/gerd",
    ),
    (
        "幽门螺杆菌要不要治",
        ["幽门螺杆菌", "HP 阳性", "吹气试验"],
        "gastroenterology",
        "体检发现幽门螺杆菌阳性不等于必须立即治疗，是否根除要结合症状、家族史与用药风险综合判断。",
        "我国感染率较高，多数人无症状。明确的根除指征包括：消化性溃疡、胃黏膜相关淋巴组织淋巴瘤、"
        "慢性胃炎伴消化不良症状、有胃癌家族史、长期服用质子泵抑制剂或非甾体抗炎药。"
        "根除通常用四联方案 10~14 天，必须按疗程服完，自行停药容易导致耐药。"
        "治疗结束需间隔至少 4 周复查呼气试验确认，期间不能吃抑酸药和抗生素。",
        ["黑便或呕血", "持续上腹痛影响进食", "不明原因贫血", "体重明显下降"],
        "明路医学顾问组",
        "https://example.cn/health/h-pylori",
    ),
    (
        "腰痛什么时候该拍片",
        ["腰疼", "腰痛", "闪了腰"],
        "orthopedics",
        "大多数急性腰痛是肌肉韧带拉伤，4~6 周内可自行缓解；没有危险信号时，早期影像检查并不改善结果。",
        "急性期不必严格卧床，适度活动恢复更快。避免弯腰搬重物、久坐，坐时腰部加靠垫。"
        "热敷可缓解肌肉紧张，急性 48 小时内可冷敷。疼痛明显时短期用非甾体抗炎药，"
        "但胃病患者需谨慎。核心肌群训练是预防复发的关键，应在疼痛缓解后开始。",
        ["下肢无力、麻木或大小便功能障碍（需紧急就诊）", "发热伴腰痛", "夜间痛醒且休息不缓解", "外伤后剧痛"],
        "明路医学顾问组",
        "https://example.cn/health/low-back-pain",
    ),
    (
        "颈椎病的日常防护",
        ["脖子疼", "颈椎", "肩颈僵硬"],
        "orthopedics",
        "长期低头使颈椎承受的负荷成倍增加，颈肩不适多与姿势相关，调整习惯比反复按摩更有效。",
        "屏幕抬高到视线平齐，避免长时间低头看手机。每 40~50 分钟起身活动颈肩，"
        "做缓慢的颈部后缩与肩胛骨内收练习。枕头高度以侧卧时头颈与脊柱成直线为宜，一般 8~12 厘米。"
        "不推荐频繁做快速旋转、甩头类动作，可能加重症状。按摩只能短期缓解，不能替代姿势调整。",
        ["手臂放射痛或手指麻木", "行走不稳、踩棉花感", "持物困难", "大小便功能异常"],
        "明路医学顾问组",
        "https://example.cn/health/cervical",
    ),
    (
        "月经周期与异常信号",
        ["月经不调", "例假", "经期"],
        "gynecology",
        "正常周期 21~35 天、经期 3~7 天。周期波动超过 7 天或经量明显改变，才需要进一步检查。",
        "记录 3 个周期的起止日期与经量（可用卫生巾更换频率粗略估计）是就诊时最有用的资料。"
        "压力、体重骤变、剧烈运动、熬夜都会影响周期。经期轻度腹痛可用热敷，"
        "疼痛影响日常可短期用止痛药。不要自行长期服用激素类调经药物。",
        ["非经期出血或绝经后出血", "经量突然大幅增多伴头晕乏力", "剧烈腹痛", "停经超过 3 个月（非妊娠）"],
        "明路医学顾问组",
        "https://example.cn/health/menstrual",
    ),
    (
        "儿童发热怎么处理",
        ["孩子发烧", "宝宝发烧", "小孩发热"],
        "pediatrics",
        "判断儿童发热的严重程度看精神状态，不看体温数字。能玩能喝、退烧后精神好，多数不必紧张。",
        "3 个月以下婴儿出现发热应立即就医。退烧药按体重计算剂量，对乙酰氨基酚适用于 3 个月以上，"
        "布洛芬适用于 6 个月以上，两者不交替使用。不建议酒精擦浴、冰敷。"
        "少量多次喂水或奶，观察尿量是否减少。发热期间出现皮疹、呕吐频繁、精神萎靡都需要就诊。",
        ["3 个月以下婴儿体温 ≥38℃", "抽搐", "呼吸急促或呻吟", "持续拒食、尿量明显减少", "皮肤出现瘀点瘀斑"],
        "明路医学顾问组",
        "https://example.cn/health/child-fever",
    ),
    (
        "干眼症的缓解",
        ["眼干", "眼睛干涩", "眼睛疲劳"],
        "ophthalmology",
        "长时间看屏幕时眨眼次数明显减少，泪液蒸发加快，是当代干眼最常见的原因。",
        "遵循 20-20-20 原则：每 20 分钟看 20 英尺（约 6 米）外 20 秒。屏幕亮度与环境光接近，"
        "位置略低于视线。有意识地完整眨眼。人工泪液可选不含防腐剂的单支装，"
        "每天使用超过 4 次时应选无防腐剂型。避免长期自行使用含血管收缩剂的『去红血丝』眼药水。",
        ["视力突然下降", "眼痛伴头痛恶心", "明显畏光流泪", "角膜接触镜佩戴者出现眼红"],
        "明路医学顾问组",
        "https://example.cn/health/dry-eye",
    ),
    (
        "慢性咽炎的诱因",
        ["嗓子不舒服", "咽炎", "喉咙有异物感"],
        "ent",
        "咽部异物感、干痒、清嗓频繁，多与鼻后滴漏、张口呼吸、胃食管反流和长期用嗓有关，单纯吃消炎药效果有限。",
        "戒烟酒、少食辛辣与过烫食物。室内保持湿度 40%~60%，避免长时间待空调房。"
        "有鼻塞流涕者先处理鼻腔问题。用嗓多的人注意发声方式，避免长时间耳语（耳语反而更费声带）。"
        "少量多次温水，含片只能短期缓解。反复清嗓会加重黏膜刺激，可用小口喝水替代。",
        ["声音嘶哑超过 2 周", "吞咽困难", "痰中带血", "颈部摸到肿块"],
        "明路医学顾问组",
        "https://example.cn/health/pharyngitis",
    ),
    (
        "焦虑的自我调节与就医时机",
        ["焦虑", "紧张", "心慌不安"],
        "psychiatry",
        "焦虑是正常情绪，只有当它持续、泛化并影响日常生活时才构成需要处理的问题。",
        "规律作息与有氧运动对焦虑的改善有明确证据，每周 3 次、每次 30 分钟中等强度即可。"
        "减少咖啡因与酒精。腹式呼吸与渐进式肌肉放松可在急性焦虑时缓解躯体症状："
        "吸气 4 秒、屏息 2 秒、呼气 6 秒，重复 5~10 轮。把担心的事写下来并区分"
        "「可行动的」与「不可控的」，能降低反复思虑。症状持续 2 周以上影响工作睡眠，建议就诊。",
        ["出现自伤或自杀念头（需立即求助）", "惊恐发作伴濒死感", "无法出门或社交", "伴持续失眠与体重下降"],
        "明路医学顾问组",
        "https://example.cn/health/anxiety",
    ),
    (
        "甲状腺结节怎么看",
        ["甲状腺结节", "脖子有包", "甲状腺"],
        "endocrinology",
        "体检发现的甲状腺结节绝大多数是良性，关键看超声报告的 TI-RADS 分级而不是大小本身。",
        "TI-RADS 3 类及以下通常 6~12 个月复查超声即可；4 类以上需评估是否穿刺。"
        "同时查甲状腺功能（TSH 等），判断有无甲亢或甲减。结节大小变化超过 20% 或出现"
        "声音嘶哑、吞咽不适需要复诊。不要自行服用含碘保健品或大量海带紫菜。",
        ["结节短期内明显增大", "声音嘶哑", "吞咽或呼吸困难", "颈部淋巴结肿大"],
        "明路医学顾问组",
        "https://example.cn/health/thyroid-nodule",
    ),
]

# PRD §4.2 四个预设 = 风格模板，不承担功能差异
PRESET_PERSONAS: list[dict] = [
    dict(name="安安", species="温和的伙伴", self_ref="我", tones=["温和", "简洁"],
         catchphrase=None, worldview="一个愿意陪你把健康事理清楚的伙伴",
         greeting="你好，今天想聊点什么？", focus=["用药", "复查"], specialty=None),
    dict(name="小简", species="爱打比方的讲解者", self_ref="我", tones=["直接", "详细"],
         catchphrase="这么说吧", worldview="擅长把指标和术语讲成人话",
         greeting="来吧，把报告给我，我翻译成人话。", focus=["饮食", "运动"], specialty=None),
    dict(name="老陈", species="盯数值的长辈", self_ref="老陈我", tones=["直接", "简短"],
         catchphrase="今天测了吗", worldview="相信数据，不喜欢拖",
         greeting="今天的数据记了吗？", focus=["用药", "复查"], specialty=None),
    dict(name="小叶", species="慢节奏的倾听者", self_ref="我", tones=["温和", "共情"],
         catchphrase=None, worldview="先把情绪接住，再谈别的",
         greeting="不急，慢慢说，我在听。", focus=["情绪", "睡眠"], specialty=None),
]


def seed(db: Session) -> None:
    """幂等：每张表空了才写。"""
    if not db.scalars(select(models.Department)).all():
        db.add_all(
            models.Department(
                code=code, name=name, consult_enabled=open_consult, description=desc, sort=index
            )
            for index, (code, name, open_consult, desc) in enumerate(DEPARTMENTS)
        )

    if not db.scalars(select(models.DepartmentSymptom)).all():
        db.add_all(
            models.DepartmentSymptom(department_code=code, symptom=symptom, weight=weight)
            for symptom, code, weight in SYMPTOM_MAP
        )

    if not db.scalars(select(models.Doctor)).all():
        db.add_all(
            models.Doctor(
                name=name,
                department_code=dept,
                title=title,
                hospital=hospital,
                specialties=list(specialties),
                sub_specialty=list(specialties),
                price_cents=price,
                avg_reply_hours=reply_hours,
                rating=rating,
                review_count=reviews,
                online_status=online,
                settle_ratio=0.7,
                license_verified=True,
                is_mock=True,  # 演示库，真实医生由后台导入
            )
            for name, dept, title, hospital, specialties, price, reply_hours, rating, reviews, online in DOCTORS
        )

    if not db.scalars(select(models.RedFlagRule)).all():
        # id 用 REDFLAG_SEED 里的稳定 slug，不用随机 UUID：
        # /v1/redflag/rules 返回的就是这个 id，ai_log.redflag_rule_id 也指向它，
        # 随机 UUID 会让客户端缓存与命中日志对不上（PRD §13.2：命中日志可回溯用的是哪一版）。
        db.add_all(
            models.RedFlagRule(
                id=rule["id"],
                name=rule["name"],
                keywords=rule["keywords"],
                logic_expr="any:" + "|".join(rule["any_of"]) if rule.get("any_of") else None,
                level=rule["level"],
                action=rule["action"],
                message=rule["message"],
                enabled=True,
                version="1",
            )
            for rule in REDFLAG_SEED
        )

    if not db.scalars(select(models.KbEntry)).all():
        reviewed = now()
        db.add_all(
            models.KbEntry(
                title=title,
                aliases=list(aliases),
                department_code=dept,
                category="health",
                summary=summary,
                content=content,
                seek_care_signs=list(signs),
                source=source,
                source_url=url,
                reviewer="医学顾问组",
                reviewer_license="（签约后填执业证书号）",
                reviewed_at=reviewed,
                valid_until=reviewed + timedelta(days=365),
                version="1",
                enabled=True,
            )
            for title, aliases, dept, summary, content, signs, source, url in KB_ENTRIES
        )

    if not db.scalars(select(models.Persona).where(models.Persona.is_preset.is_(True))).all():
        db.add_all(
            models.Persona(
                is_preset=True, source="preset", review_status="approved", created_at=now(), **preset
            )
            for preset in PRESET_PERSONAS
        )

    db.commit()
