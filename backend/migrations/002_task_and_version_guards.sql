CREATE TRIGGER immutable_reports BEFORE UPDATE ON report_versions
BEGIN
    SELECT RAISE(ABORT, 'Report versions are immutable');
END;
CREATE TRIGGER immutable_configs BEFORE UPDATE ON evaluation_configs
BEGIN
    SELECT RAISE(ABORT, 'Evaluation configurations are immutable');
END;
CREATE TRIGGER immutable_published_tasks BEFORE UPDATE ON task_definitions WHEN OLD.status != 'draft'
BEGIN
    SELECT RAISE(ABORT, 'Published and source task versions are immutable');
END;
INSERT INTO task_definitions(id,version,title,instructions,rubric,tests,metadata,status,course_id,owner_user_id)
VALUES ('campus-sales-v1','1.0','校园商店销售数据清洗与分析',
        '读取订单 CSV。排除缺失必填值和异常日期。按订单号去重，保留退款并把退款按负金额计入月净销售额。提交 Python 脚本、清洗结果、趋势图和做法说明。',
        '[]','{}',
        '{"minutes":45,"deliverables":["Python 脚本","清洗结果","趋势图","做法和局限说明"],"bundle":"campus-sales"}',
        'published','demo-course','teacher-demo');
