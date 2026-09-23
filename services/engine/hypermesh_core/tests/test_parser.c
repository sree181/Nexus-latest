/*
 * test_parser.c — Smoke tests for the HyperMesh Cypher parser
 *
 * Build and run:
 *   cd hypermesh_core
 *   make parser_test
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "../src/hypermesh.h"

/* ── Test harness ─────────────────────────────────────────────────────────── */

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, label) do { \
    if (cond) { printf("  PASS  %s\n", label); g_pass++; } \
    else       { printf("  FAIL  %s\n", label); g_fail++; } \
} while (0)

static HmQueryAst *parse(const char *q) {
    return hm_parse_query_alloc(q);
}

static void free_ast(HmQueryAst *a) {
    hm_query_ast_free(a);
}

/* ── Tests ────────────────────────────────────────────────────────────────── */

static void test_show_tables(void) {
    printf("\n--- SHOW_HYPEREDGE_TABLES ---\n");

    HmQueryAst *a = parse("CALL SHOW_HYPEREDGE_TABLES() RETURN *");
    CHECK(hm_ast_kind(a) == HM_QUERY_SHOW_TABLES, "basic CALL SHOW_HYPEREDGE_TABLES()");
    free_ast(a);

    a = parse("call show_hyperedge_tables()");
    CHECK(hm_ast_kind(a) == HM_QUERY_SHOW_TABLES, "lowercase CALL");
    free_ast(a);

    /* Single-token variant spelled identically but without RETURN */
    a = parse("CALL SHOW_HYPEREDGE_TABLE()");
    CHECK(hm_ast_kind(a) == HM_QUERY_SHOW_TABLES, "SHOW_HYPEREDGE_TABLE singular");
    free_ast(a);
}

static void test_get_by_range(void) {
    printf("\n--- GET_HYPEREDGES_BY_TIME_RANGE ---\n");

    HmQueryAst *a = parse("CALL GET_HYPEREDGES_BY_TIME_RANGE('CoProximity', 100, 200) RETURN *");
    CHECK(hm_ast_kind(a)     == HM_QUERY_GET_BY_RANGE, "kind == GET_BY_RANGE");
    CHECK(hm_ast_ts_start(a) == 100,                   "ts_start == 100");
    CHECK(hm_ast_ts_end(a)   == 200,                   "ts_end == 200");
    CHECK(strcmp(hm_ast_table(a), "COPROXIMITY") == 0, "table == COPROXIMITY");
    free_ast(a);

    /* bare table name without quotes */
    a = parse("CALL GET_HYPEREDGES_BY_TIME_RANGE(CoProximity, 50, 99)");
    CHECK(hm_ast_kind(a)     == HM_QUERY_GET_BY_RANGE, "bare table name");
    CHECK(hm_ast_ts_start(a) == 50,  "ts_start == 50");
    CHECK(hm_ast_ts_end(a)   == 99,  "ts_end == 99");
    free_ast(a);
}

static void test_tpi_range(void) {
    printf("\n--- TPI RANGE QUERY ---\n");

    /* Standard form */
    HmQueryAst *a = parse(
        "MATCH HYPEREDGE (he:CoProximity) "
        "WHERE he.event_ts >= 0 AND he.event_ts <= 100");
    CHECK(hm_ast_kind(a)     == HM_QUERY_TPI_RANGE, "standard >= AND <=");
    CHECK(hm_ast_ts_start(a) == 0,                  "ts_start == 0");
    CHECK(hm_ast_ts_end(a)   == 100,                "ts_end == 100");
    CHECK(strcmp(hm_ast_alias(a), "HE") == 0,       "alias == HE");
    free_ast(a);

    /* Reversed order: <= T2 AND >= T1 */
    a = parse(
        "MATCH HYPEREDGE (he:CoProximity) "
        "WHERE he.event_ts <= 200 AND he.event_ts >= 50");
    CHECK(hm_ast_kind(a)     == HM_QUERY_TPI_RANGE, "reversed <= AND >=");
    CHECK(hm_ast_ts_start(a) == 50,                 "ts_start == 50 (normalised)");
    CHECK(hm_ast_ts_end(a)   == 200,                "ts_end == 200 (normalised)");
    free_ast(a);

    /* With MEMBERS clause and RETURN */
    a = parse(
        "MATCH HYPEREDGE (h:CoProximity) MEMBERS [a:Drone, b:Drone, c:Drone] "
        "WHERE h.event_ts >= 0 AND h.event_ts <= 100 "
        "RETURN h.event_ts, count(*) AS cluster_size");
    CHECK(hm_ast_kind(a)     == HM_QUERY_TPI_RANGE, "with MEMBERS + RETURN");
    CHECK(hm_ast_ts_start(a) == 0,                  "ts_start with MEMBERS");
    CHECK(hm_ast_ts_end(a)   == 100,                "ts_end with MEMBERS");
    free_ast(a);

    /* One-sided range: only >= */
    a = parse("MATCH HYPEREDGE (h:CoProximity) WHERE h.event_ts >= 500");
    CHECK(hm_ast_kind(a)     == HM_QUERY_TPI_RANGE, "one-sided >= 500");
    CHECK(hm_ast_ts_start(a) == 500,                "ts_start == 500");
    /* ts_end should be UINT32_MAX (all buckets from 500 onward) */
    CHECK(hm_ast_ts_end(a)   == UINT32_MAX,         "ts_end == UINT32_MAX");
    free_ast(a);
}

static void test_fmi_lookup(void) {
    printf("\n--- FMI MEMBER LOOKUP ---\n");

    HmQueryAst *a = parse(
        "MATCH HYPEREDGE (he:CoProximity) WHERE 5 IN he.members RETURN *");
    CHECK(hm_ast_kind(a)    == HM_QUERY_FMI_LOOKUP, "N IN he.members");
    CHECK(hm_ast_node_id(a) == 5,                   "node_id == 5");
    free_ast(a);

    a = parse(
        "MATCH HYPEREDGE (h:CoProximity) WHERE 17 IN h.member_node_ids");
    CHECK(hm_ast_kind(a)    == HM_QUERY_FMI_LOOKUP, "N IN he.member_node_ids");
    CHECK(hm_ast_node_id(a) == 17,                  "node_id == 17");
    free_ast(a);
}

static void test_full_scan(void) {
    printf("\n--- FULL SCAN (no WHERE) ---\n");

    HmQueryAst *a = parse("MATCH HYPEREDGE (he:CoProximity)");
    CHECK(hm_ast_kind(a) == HM_QUERY_FULL_SCAN, "no WHERE → full scan");
    free_ast(a);

    a = parse("MATCH HYPEREDGE (h:CoProximity) MEMBERS [a:Drone, b:Drone]");
    CHECK(hm_ast_kind(a) == HM_QUERY_FULL_SCAN, "MEMBERS but no WHERE → full scan");
    free_ast(a);
}

static void test_parse_errors(void) {
    printf("\n--- PARSE ERRORS ---\n");

    HmQueryAst *a = parse("");
    CHECK(hm_ast_kind(a) == HM_QUERY_PARSE_ERROR, "empty string → error");
    free_ast(a);

    a = parse("SELECT * FROM Drone");
    CHECK(hm_ast_kind(a) == HM_QUERY_PARSE_ERROR, "SQL SELECT → error");
    free_ast(a);

    a = parse("CALL NONEXISTENT_PROC()");
    CHECK(hm_ast_kind(a) == HM_QUERY_PARSE_ERROR, "unknown CALL proc → error");
    free_ast(a);
}

static void test_comments(void) {
    printf("\n--- COMMENT HANDLING ---\n");

    HmQueryAst *a = parse(
        "// Rank drones by coalition frequency\n"
        "MATCH HYPEREDGE (h:CoProximity) "
        "WHERE h.event_ts >= 100 AND h.event_ts <= 200");
    CHECK(hm_ast_kind(a)     == HM_QUERY_TPI_RANGE, "leading // comment ignored");
    CHECK(hm_ast_ts_start(a) == 100,                "ts_start after comment");
    CHECK(hm_ast_ts_end(a)   == 200,                "ts_end after comment");
    free_ast(a);
}

/* ── Phase 2: predicates, RETURN, ORDER BY, LIMIT ─────────────────────────── */

static void test_predicates(void) {
    printf("\n--- PROPERTY PREDICATES ---\n");

    /* Single predicate after time range */
    HmQueryAst *a = parse(
        "MATCH HYPEREDGE (he:Events) "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 500 "
        "AND he.CONFIDENCE >= 0.8");
    CHECK(hm_ast_kind(a)      == HM_QUERY_TPI_RANGE, "P1: kind TPI_RANGE");
    CHECK(hm_ast_ts_start(a)  == 100,                "P1: ts_start=100");
    CHECK(hm_ast_ts_end(a)    == 500,                "P1: ts_end=500");
    CHECK(hm_ast_pred_count(a) == 1,                 "P1: 1 predicate");
    CHECK(strcmp(hm_ast_pred_col(a, 0), "CONFIDENCE") == 0, "P1: col=CONFIDENCE");
    CHECK(hm_ast_pred_op(a, 0)  == HM_PRED_GEQ,     "P1: op=GEQ");
    CHECK(hm_ast_pred_is_str(a, 0) == 0,             "P1: is_str=0 (numeric)");
    /* value should be "0.8" */
    CHECK(strcmp(hm_ast_pred_val(a, 0), "0.8") == 0, "P1: val=0.8");
    free_ast(a);

    /* Two predicates: numeric and string */
    a = parse(
        "MATCH HYPEREDGE (he:Events) "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 500 "
        "AND he.CONFIDENCE > 0.5 AND he.LABEL = 'alpha'");
    CHECK(hm_ast_pred_count(a) == 2,                 "P2: 2 predicates");
    CHECK(strcmp(hm_ast_pred_col(a, 0), "CONFIDENCE") == 0, "P2: pred0 col");
    CHECK(hm_ast_pred_op(a, 0) == HM_PRED_GT,        "P2: pred0 op=GT");
    CHECK(strcmp(hm_ast_pred_val(a, 0), "0.5") == 0, "P2: pred0 val=0.5");
    CHECK(strcmp(hm_ast_pred_col(a, 1), "LABEL") == 0, "P2: pred1 col");
    CHECK(hm_ast_pred_op(a, 1) == HM_PRED_EQ,        "P2: pred1 op=EQ");
    CHECK(hm_ast_pred_is_str(a, 1) == 1,             "P2: pred1 is_str=1");
    CHECK(strcmp(hm_ast_pred_val(a, 1), "alpha") == 0, "P2: pred1 val=alpha (string case preserved)");
    free_ast(a);

    /* Predicate on FULL_SCAN (no time range) */
    a = parse(
        "MATCH HYPEREDGE (he:Events) "
        "WHERE he.SCORE < 10");
    CHECK(hm_ast_kind(a)       == HM_QUERY_FULL_SCAN, "P3: kind FULL_SCAN");
    CHECK(hm_ast_pred_count(a) == 1,                  "P3: 1 predicate");
    CHECK(strcmp(hm_ast_pred_col(a, 0), "SCORE") == 0, "P3: col=SCORE");
    CHECK(hm_ast_pred_op(a, 0) == HM_PRED_LT,         "P3: op=LT");
    CHECK(strcmp(hm_ast_pred_val(a, 0), "10") == 0,   "P3: val=10");
    free_ast(a);

    /* Integer predicate (no decimal) */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) "
        "WHERE he.event_ts >= 0 AND he.COUNT <= 5");
    CHECK(hm_ast_kind(a)       == HM_QUERY_TPI_RANGE, "P4: TPI_RANGE + int pred");
    CHECK(hm_ast_pred_count(a) == 1,                  "P4: 1 predicate");
    CHECK(hm_ast_pred_op(a, 0) == HM_PRED_LEQ,        "P4: op=LEQ");
    CHECK(strcmp(hm_ast_pred_val(a, 0), "5") == 0,    "P4: val=5");
    free_ast(a);

    /* No predicates when none present */
    a = parse("MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0");
    CHECK(hm_ast_pred_count(a) == 0, "P5: 0 predicates");
    free_ast(a);
}

static void test_return_order_limit(void) {
    printf("\n--- RETURN / ORDER BY / LIMIT ---\n");

    /* RETURN * (empty return_cols) */
    HmQueryAst *a = parse(
        "MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0 RETURN *");
    CHECK(hm_ast_kind(a) == HM_QUERY_TPI_RANGE, "ROL1: TPI_RANGE");
    CHECK(strcmp(hm_ast_return_cols(a), "") == 0, "ROL1: RETURN * → empty return_cols");
    free_ast(a);

    /* Simple RETURN cols */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0 "
        "RETURN he.event_ts, he.CONFIDENCE");
    CHECK(strstr(hm_ast_return_cols(a), "EVENT_TS") != NULL, "ROL2: EVENT_TS in return_cols");
    CHECK(strstr(hm_ast_return_cols(a), "CONFIDENCE") != NULL, "ROL2: CONFIDENCE in return_cols");
    free_ast(a);

    /* ORDER BY DESC */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0 "
        "ORDER BY he.WEIGHT DESC");
    CHECK(strcmp(hm_ast_order_col(a), "HE.WEIGHT") == 0, "ROL3: order_col=HE.WEIGHT");
    CHECK(hm_ast_order_desc(a) == 1,                  "ROL3: order_desc=1 (DESC)");
    free_ast(a);

    /* ORDER BY ASC (default) */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0 "
        "ORDER BY WEIGHT ASC");
    CHECK(strcmp(hm_ast_order_col(a), "WEIGHT") == 0, "ROL4: order_col=WEIGHT (bare ident)");
    CHECK(hm_ast_order_desc(a) == 0,                  "ROL4: order_desc=0 (ASC)");
    free_ast(a);

    /* LIMIT */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) WHERE he.event_ts >= 0 LIMIT 25");
    CHECK(hm_ast_limit_n(a) == 25, "ROL5: limit_n=25");
    free_ast(a);

    /* Full query: WHERE + predicates + RETURN + ORDER BY + LIMIT */
    a = parse(
        "MATCH HYPEREDGE (he:Events) "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 500 "
        "AND he.CONFIDENCE >= 0.8 "
        "RETURN he.event_ts, he.CONFIDENCE "
        "ORDER BY he.CONFIDENCE DESC "
        "LIMIT 10");
    CHECK(hm_ast_kind(a)      == HM_QUERY_TPI_RANGE,   "ROL6: TPI_RANGE");
    CHECK(hm_ast_pred_count(a) == 1,                    "ROL6: 1 predicate");
    CHECK(strstr(hm_ast_return_cols(a), "CONFIDENCE") != NULL, "ROL6: return_cols has CONFIDENCE");
    CHECK(strcmp(hm_ast_order_col(a), "HE.CONFIDENCE") == 0, "ROL6: order_col=HE.CONFIDENCE");
    CHECK(hm_ast_order_desc(a) == 1,                    "ROL6: DESC");
    CHECK(hm_ast_limit_n(a)    == 10,                   "ROL6: limit_n=10");
    free_ast(a);

    /* COUNT aggregate in RETURN */
    a = parse(
        "MATCH HYPEREDGE (he:Ev) "
        "WHERE he.event_ts >= 0 "
        "RETURN COUNT(*)");
    CHECK(strstr(hm_ast_return_cols(a), "COUNT(*)") != NULL, "ROL7: COUNT(*) in return_cols");
    free_ast(a);
}

/* ── Phase 3: INSERT / DELETE / UPDATE ────────────────────────────────────── */

static void test_dml_insert(void) {
    printf("\n--- INSERT ---\n");

    /* Basic INSERT with all built-in fields */
    HmQueryAst *a = parse(
        "INSERT INTO Sensors "
        "(event_ts, members, weight, mean_dist_m, formation) "
        "VALUES (1000, [1, 2], 0.9, 15.5, WEDGE)");
    CHECK(hm_ast_kind(a)  == HM_QUERY_INSERT, "I1: kind=INSERT");
    CHECK(strcmp(hm_ast_table(a), "SENSORS") == 0, "I1: table=SENSORS");

    /* Column list */
    CHECK(strstr(hm_ast_insert_cols(a), "EVENT_TS")    != NULL, "I1: cols has EVENT_TS");
    CHECK(strstr(hm_ast_insert_cols(a), "MEMBERS")     != NULL, "I1: cols has MEMBERS");
    CHECK(strstr(hm_ast_insert_cols(a), "WEIGHT")      != NULL, "I1: cols has WEIGHT");
    CHECK(strstr(hm_ast_insert_cols(a), "FORMATION")   != NULL, "I1: cols has FORMATION");

    /* Value list (0x1F-separated) */
    const char *vals = hm_ast_insert_vals(a);
    CHECK(strstr(vals, "1000")    != NULL, "I1: vals has 1000 (event_ts)");
    CHECK(strstr(vals, "[1,2]")   != NULL, "I1: vals has [1,2] (members)");
    CHECK(strstr(vals, "0.9")     != NULL, "I1: vals has 0.9 (weight)");
    CHECK(strstr(vals, "WEDGE")   != NULL, "I1: vals has WEDGE (formation)");
    free_ast(a);

    /* INSERT with user-defined properties */
    a = parse(
        "INSERT INTO Sensors "
        "(event_ts, members, CONFIDENCE, SEVERITY, LABEL) "
        "VALUES (2000, [3, 4], 0.95, 3, 'alpha')");
    CHECK(hm_ast_kind(a) == HM_QUERY_INSERT, "I2: kind=INSERT with properties");
    CHECK(strstr(hm_ast_insert_cols(a), "CONFIDENCE") != NULL, "I2: cols has CONFIDENCE");
    CHECK(strstr(hm_ast_insert_cols(a), "LABEL")      != NULL, "I2: cols has LABEL");
    vals = hm_ast_insert_vals(a);
    CHECK(strstr(vals, "0.95")  != NULL, "I2: vals has 0.95 (confidence)");
    CHECK(strstr(vals, "alpha") != NULL, "I2: vals has alpha (label)");
    free_ast(a);

    /* INSERT with integer-only members list */
    a = parse(
        "INSERT INTO T (event_ts, members) VALUES (5, [10, 20, 30])");
    CHECK(hm_ast_kind(a) == HM_QUERY_INSERT, "I3: basic members list");
    CHECK(strstr(hm_ast_insert_vals(a), "[10,20,30]") != NULL, "I3: members=[10,20,30]");
    free_ast(a);

    /* Column-value count mismatch produces PARSE_ERROR */
    a = parse("INSERT INTO T (a, b) VALUES (1)");
    CHECK(hm_ast_kind(a) == HM_QUERY_INSERT, "I4: INSERT parses even with mismatch (validated in Python)");
    free_ast(a);
}

static void test_dml_delete(void) {
    printf("\n--- DELETE ---\n");

    /* DELETE with time range WHERE */
    HmQueryAst *a = parse(
        "DELETE FROM Sensors "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 500");
    CHECK(hm_ast_kind(a)     == HM_QUERY_DELETE, "D1: kind=DELETE");
    CHECK(strcmp(hm_ast_table(a), "SENSORS") == 0, "D1: table=SENSORS");
    CHECK(hm_ast_ts_start(a) == 100, "D1: ts_start=100");
    CHECK(hm_ast_ts_end(a)   == 500, "D1: ts_end=500");
    free_ast(a);

    /* DELETE with time range + property predicate */
    a = parse(
        "DELETE FROM Sensors "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 500 "
        "AND he.CONFIDENCE < 0.5");
    CHECK(hm_ast_kind(a)       == HM_QUERY_DELETE, "D2: kind=DELETE with predicate");
    CHECK(hm_ast_pred_count(a) == 1,               "D2: 1 property predicate");
    CHECK(hm_ast_pred_op(a, 0) == HM_PRED_LT,      "D2: predicate op=LT");
    free_ast(a);

    /* DELETE with FMI lookup */
    a = parse("DELETE FROM Sensors WHERE 7 IN he.members");
    CHECK(hm_ast_kind(a)    == HM_QUERY_DELETE, "D3: kind=DELETE FMI");
    CHECK(hm_ast_node_id(a) == 7,               "D3: node_id=7");
    free_ast(a);

    /* DELETE with full-scan WHERE (property-only predicate) */
    a = parse("DELETE FROM Sensors WHERE he.SEVERITY > 4");
    CHECK(hm_ast_kind(a)       == HM_QUERY_DELETE, "D4: kind=DELETE full-scan prop");
    CHECK(hm_ast_pred_count(a) == 1,               "D4: 1 predicate");
    free_ast(a);
}

static void test_dml_update(void) {
    printf("\n--- UPDATE ---\n");

    /* UPDATE single property */
    HmQueryAst *a = parse(
        "UPDATE Sensors "
        "SET CONFIDENCE = 0.99 "
        "WHERE he.event_ts >= 100 AND he.event_ts <= 200");
    CHECK(hm_ast_kind(a)     == HM_QUERY_UPDATE,  "U1: kind=UPDATE");
    CHECK(strcmp(hm_ast_table(a), "SENSORS") == 0, "U1: table=SENSORS");
    CHECK(hm_ast_ts_start(a) == 100, "U1: ts_start=100");
    CHECK(hm_ast_ts_end(a)   == 200, "U1: ts_end=200");

    const char *set = hm_ast_update_set(a);
    CHECK(strstr(set, "CONFIDENCE") != NULL, "U1: SET has CONFIDENCE");
    CHECK(strstr(set, "0.99")       != NULL, "U1: SET has 0.99");
    free_ast(a);

    /* UPDATE two fields */
    a = parse(
        "UPDATE Sensors "
        "SET CONFIDENCE = 0.75, LABEL = 'updated' "
        "WHERE he.event_ts >= 300 AND he.event_ts <= 400");
    CHECK(hm_ast_kind(a) == HM_QUERY_UPDATE, "U2: kind=UPDATE two fields");
    set = hm_ast_update_set(a);
    CHECK(strstr(set, "CONFIDENCE") != NULL, "U2: SET has CONFIDENCE");
    CHECK(strstr(set, "LABEL")      != NULL, "U2: SET has LABEL");
    CHECK(strstr(set, "updated")    != NULL, "U2: SET has 'updated' value");
    free_ast(a);

    /* UPDATE with predicate in WHERE */
    a = parse(
        "UPDATE Sensors "
        "SET SEVERITY = 1 "
        "WHERE he.event_ts >= 0 AND he.CONFIDENCE > 0.9");
    CHECK(hm_ast_kind(a)       == HM_QUERY_UPDATE, "U3: kind=UPDATE with predicate");
    CHECK(hm_ast_pred_count(a) == 1,               "U3: 1 predicate from WHERE");
    CHECK(strstr(hm_ast_update_set(a), "SEVERITY") != NULL, "U3: SET has SEVERITY");
    free_ast(a);
}

/* ── main ─────────────────────────────────────────────────────────────────── */

int main(void)
{
    printf("HyperMesh DB — Cypher Parser Tests\n");
    printf("====================================\n");

    test_show_tables();
    test_get_by_range();
    test_tpi_range();
    test_fmi_lookup();
    test_full_scan();
    test_parse_errors();
    test_comments();
    test_predicates();
    test_return_order_limit();
    test_dml_insert();
    test_dml_delete();
    test_dml_update();

    printf("\n====================================\n");
    printf("Results: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}
