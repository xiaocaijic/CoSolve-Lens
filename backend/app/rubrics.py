from typing import Literal


FIVE_VERSION = "bars-2.0-five-dimension"
FIVE_POINT_VERSION = "bars-3.0-five-point"
FIVE_SETTINGS = {"rubric_version": FIVE_POINT_VERSION, "prompt_version": "prompt-4.1-calibrated-guidance",
                 "ruleset_version": "rules-3.0-five-point"}
FIVE_DIMENSION_VERSIONS = (FIVE_VERSION, FIVE_POINT_VERSION)

LEGACY_DIMENSIONS = {
    "organization": "任务理解与组织", "verification": "检查与验证",
    "diagnosis": "诊断与调整", "ai_integration": "AI 使用与整合",
}
DIMENSIONS = {
    "problem_understanding": "问题理解", "information_discernment": "信息甄别",
    "analytical_reasoning": "分析推理", "result_evaluation": "结果评价", "strategy_adjustment": "策略调整",
}
FiveDimensionName = Literal["problem_understanding", "information_discernment", "analytical_reasoning",
                            "result_evaluation", "strategy_adjustment"]
DimensionName = FiveDimensionName | Literal["organization", "verification", "diagnosis", "ai_integration"]
BEHAVIORS = {"adopt": "采用", "modify": "修改", "question": "质疑", "reject": "拒绝",
             "verify": "验证", "reask": "重新提问"}
BehaviorName = Literal["adopt", "modify", "question", "reject", "verify", "reask"]

FIVE_BARS = {
    "problem_understanding": [
        "已有证据表明核心目标、输入输出或限制有实质误解，仍沿错误前提推进。",
        "说出部分目标或输入输出，任务范围、限制或分解尚不清楚。",
        "正确界定主要目标、输入输出及关键限制，能将任务分成可执行步骤。",
        "明确目标、边界与约束，说明子任务如何衔接或如何处理歧义，并据此推进。",
    ],
    "information_discernment": [
        "已有证据表明采纳了与明确任务条件冲突的信息或建议，且没有辨析。",
        "对信息或建议提出疑问或作出选择，适用性依据较少。",
        "依据任务条件、概念或可核查来源判断主要信息或建议的适用性，说明取舍。",
        "系统比较关键信息或建议的依据、适用边界与局限，解释信任或拒绝的条件。",
    ],
    "analytical_reasoning": [
        "已有证据表明使用明显不成立的推理或错误因果关系，且未纠正。",
        "识别局部现象或给出初步解释，推理链或比较依据仍有缺口。",
        "依据概念或证据解释主要因果、比较方案或定位错误，结论与证据相符。",
        "建立连贯推理链，检验或排除有竞争力的解释，并说明条件与结论间的关系。",
    ],
    "result_evaluation": [
        "已有证据表明面对明确不一致仍把结果当作可靠，或使用不成立的核验。",
        "提出具体核验问题、请求有目标的测试，或检查单个结果，依据或结论仍有限。",
        "用对照、样例、反例或明确标准检查结果，并解释至少一项核验结论。",
        "综合核验主要与边界结果，基于证据解释最终答案的可靠性、局限或残余风险。",
    ],
    "strategy_adjustment": [
        "已有证据表明收到明确反证或反馈后仍重复无效做法，未作有意义调整。",
        "根据反馈缩小问题或尝试改变一个步骤，调整原因或后续效果较少。",
        "能依据反馈改变提问、实现路线或资源使用，改变与问题相符。",
        "主动选择并调整策略，说明取舍依据，并用后续反馈核对调整效果。",
    ],
}

FIVE_MISSING = {
    "problem_understanding": "MISSING_PROBLEM_EVIDENCE", "information_discernment": "MISSING_DISCERNMENT_EVIDENCE",
    "analytical_reasoning": "MISSING_REASONING_EVIDENCE", "result_evaluation": "MISSING_RESULT_EVIDENCE",
    "strategy_adjustment": "MISSING_STRATEGY_EVIDENCE",
}
FIVE_POINT_ADVANCED = {
    "problem_understanding": "准确界定任务和关键限制，说明主要子任务的衔接，并处理一项具体的边界或歧义。",
    "information_discernment": "以具体条件或来源比较两项信息或建议，并说明为何选择其中一项。",
    "analytical_reasoning": "解释主要因果或定位错误，并用一个对照或数据检验自己的解释。",
    "result_evaluation": "使用明确标准检查主要结果和至少一个边界或反例，并说明检查所得结论。",
    "strategy_adjustment": "说明为什么依据反馈更换步骤或方法，并检查调整后的一个可观察结果。",
}
FIVE_POINT_BARS = {name: [*anchors[:3], FIVE_POINT_ADVANCED[name], anchors[3]]
                   for name, anchors in FIVE_BARS.items()}
FIVE_NO_OPPORTUNITY = {name: "NO_" + name.upper() + "_OPPORTUNITY" for name in DIMENSIONS}


def is_five_dimension(version):
    return version in FIVE_DIMENSION_VERSIONS


def level_bounds(version):
    return (1, 5) if version == FIVE_POINT_VERSION else (0, 3)


def rubric_dimensions(version):
    return DIMENSIONS if is_five_dimension(version) else LEGACY_DIMENSIONS
