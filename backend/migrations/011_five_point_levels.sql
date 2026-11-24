ALTER TABLE dimension_results RENAME TO dimension_results_previous;
CREATE TABLE dimension_results (
    report_id TEXT NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL, state TEXT NOT NULL, level INTEGER, rationale TEXT NOT NULL,
    PRIMARY KEY(report_id, dimension),
    CHECK((state='scored' AND level BETWEEN 0 AND 5 AND level IS NOT NULL) OR
          (state IN ('not_applicable','insufficient_evidence') AND level IS NULL))
);
INSERT INTO dimension_results SELECT * FROM dimension_results_previous;
DROP TABLE dimension_results_previous;
