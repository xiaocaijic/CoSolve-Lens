INSERT INTO task_definitions(id,version,title,instructions,rubric,tests,metadata,status,course_id,owner_user_id)
VALUES
('studychat-s25-a3-v1','1.0','StudyChat A3 · Data Cleaning and Preprocessing',
 '读取随任务提供的两份 CKD 表格，检查 ID 重复、空值和列类型。按 ID 合并，说明选择连接方式的理由，完成至少一项类别编码和一项数值处理。交付脚本、合并后 CSV、清洗前后行数与缺失情况。说明一个局限。请使用本地资料，题目与作者归属见原版说明。',
 '[]','[]','{"bundle":"studychat-s25-a3-v1","minutes":40,"task_type":"数据清洗","difficulty":"中级","ai_allowed":true,"goal":"合并两份质量不一的表格，说明如何处理重复和缺失。","attribution":"Aryan Tipnis、Hunter McNichols · UMass Amherst COMPSCI 383 Artificial Intelligence · CC BY 4.0 · S25 A3，本地课堂节选。","materials":["chronic_kidney_disease_categorical.csv","chronic_kidney_disease_numerical.csv","CS_383_Data_Cleaning_Assignment.ipynb","README.md"],"deliverables":["Python 脚本","合并后 CSV","清洗检查记录","局限说明"],"result_label":"合并后 CSV","check_label":"清洗前后行数与缺失检查","ai_hint":"先比较两个 ID 集合，分别记录共同 ID 和单侧 ID。选一个重复 ID 和一个只出现于一侧的 ID，预测合并后的行数再核对。"}',
 'published','demo-course','teacher-demo'),
('studychat-s25-a2-v1','1.0','StudyChat A2 · SearchComplete',
 '使用本地 genZ.txt、test.txt 和起始脚本构建前缀树。用 BFS、DFS 和 UCS 生成联想词，在同一个前缀上记录结果顺序并解释差异。空前缀、未知前缀和同代价排序至少选择两种作为边界检查。现场版可提交整合后的单个 Python 文件。',
 '[]','[]','{"bundle":"studychat-s25-a2-v1","minutes":55,"task_type":"搜索算法","difficulty":"较难","ai_allowed":true,"goal":"实现三种前缀检索策略，用同一输入对照排序。","attribution":"Aditya Singh、Hunter McNichols · UMass Amherst COMPSCI 383 Artificial Intelligence · CC BY 4.0 · S25 A2，本地课堂节选。","materials":["genZ.txt","test.txt","autocomplete.py","main.py","utilities.py","README.md"],"deliverables":["Python 脚本","三种策略输出对照","边界样例","搜索顺序解释"],"result_label":"三种策略的输出对照","check_label":"边界样例与预期输出","ai_hint":"先拿一个有两个分支、其中一条较深的微型前缀树手推。对同一个前缀分别记录队列、栈与优先队列的弹出次序，再与程序结果比较。"}',
 'published','demo-course','teacher-demo'),
('studychat-f24-a5-v1','1.0','StudyChat A5 · Judging Flowers',
 '使用本地 iris.csv，以相同训练／测试划分比较逻辑回归与 k 近邻。固定随机种子；如果使用缩放器，只在训练集拟合。提交可复现脚本、两种模型的准确率与混淆矩阵，并选一类错误解释。现场节选完成两种模型即可。',
 '[]','[]','{"bundle":"studychat-f24-a5-v1","minutes":45,"task_type":"分类建模","difficulty":"中级","ai_allowed":true,"goal":"用相同数据划分比较两种分类模型并解释错误。","attribution":"Hunter McNichols · UMass Amherst COMPSCI 383 Artificial Intelligence · CC BY 4.0 · F24 A5，本地课堂节选。","materials":["iris.csv","flowers_sample_notebook.ipynb","flowers.jpeg","README.md"],"deliverables":["Python 脚本","准确率与混淆矩阵","数据划分和复现说明"],"result_label":"准确率与混淆矩阵","check_label":"划分、随机种子与错误检查","ai_hint":"两种分类器应使用同一次划分。检查缩放器只从训练集学习参数，并选混淆矩阵中一个非对角格，说明实际类别和预测类别。"}',
 'published','demo-course','teacher-demo');

CREATE TABLE analysis_batches (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    model TEXT NOT NULL CHECK(model IN ('rules','deepseek-flash')),
    provider_call_limit INTEGER NOT NULL CHECK(provider_call_limit BETWEEN 0 AND 100),
    provider_calls_started INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE evaluation_progress (
    run_id TEXT PRIMARY KEY REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('dialogue','evaluation','evidence','recommendations')),
    updated_at TEXT NOT NULL
);
