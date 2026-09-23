/*
 * parser.h — HyperMesh Cypher Recursive-Descent Parser
 *
 * Parses the subset of openCypher + HyperMesh DDL into an HmQueryAst.
 *
 * Supported grammar:
 *
 *   Query :=
 *       "CALL" "SHOW_HYPEREDGE_TABLES" "(" ")" ( "RETURN" "*" )?
 *     | "CALL" "GET_HYPEREDGES_BY_TIME_RANGE" "(" TableRef "," Int "," Int ")"
 *           ( "RETURN" "*" )?
 *     | "MATCH" "HYPEREDGE" "(" Alias ":" TableName ")"
 *           ( "MEMBERS" "[" MemberList "]" )?
 *           ( "WHERE" WhereExpr )?
 *           ( "RETURN" ReturnExpr )?
 *           ( "ORDER" "BY" PropRef ( "ASC" | "DESC" )? )?
 *           ( "LIMIT" Int )?
 *
 *     | "MATCH" "HYPEREDGE" "(" Alias1 ":" TableName1 ")" ","
 *               "HYPEREDGE" "(" Alias2 ":" TableName2 ")"
 *           ( "WHERE" JoinWhereExpr )?
 *           ( "RETURN" ReturnExpr )?
 *           ( "ORDER" "BY" PropRef ( "ASC" | "DESC" )? )?
 *           ( "LIMIT" Int )?
 *
 *     | "CREATE" "HYPEREDGE" "TABLE" Name "(" MemberTableList ")"
 *           ( "PROPERTIES" "(" PropList ")" )?
 *           ( "BUCKET_SECONDS" Int )?
 *           ( "COMPACT_THRESHOLD" Int )?
 *     | "DROP" "HYPEREDGE" "TABLE" Name
 *     | "INSERT" "INTO" Name "(" ColList ")" "VALUES" "(" ValList ")"
 *     | "DELETE" "FROM" Name "WHERE" WhereExpr
 *     | "UPDATE" Name "SET" SetList "WHERE" WhereExpr
 *
 *   ColList  := Ident ( "," Ident )*
 *   ValList  := Value  ( "," Value )*
 *   Value    := Int | NumVal | String | Ident | "[" Int ("," Int)* "]"
 *   SetList  := Ident "=" Value ( "," Ident "=" Value )*
 *
 *   WhereExpr :=
 *       PropRef (">=" | ">" | "<=" | "<") NumVal
 *           ( "AND" PropRef ("=" | ">=" | ">" | "<=" | "<") (NumVal | String) )*
 *     | NumVal "IN" PropRef
 *
 *   JoinWhereExpr :=
 *       SingleTablePredicate ( "AND" SingleTablePredicate | "AND" CrossAliasPredicate )*
 *     | Int "IN" AliasRef.members [ "AND" Int "IN" AliasRef.members ]
 *
 *   SingleTablePredicate := AliasRef.prop op (NumVal | String)
 *   CrossAliasPredicate  := AliasRef.prop op AliasRef.prop
 *
 *   NumVal     := Int ( "." Int )?   -- float literal via INT DOT INT
 *   ReturnExpr := "*"
 *               | AggExpr ( "," AggExpr )*
 *   AggExpr    := PropRef
 *               | "COUNT" "(" "*" ")"
 *               | ( "AVG" | "MAX" | "MIN" | "SUM" ) "(" PropRef ")"
 *   PropRef    := Ident "." Ident
 *
 * Anything not matched produces HM_QUERY_PARSE_ERROR.
 * A MATCH HYPEREDGE with no recognisable WHERE becomes HM_QUERY_FULL_SCAN.
 *
 * AST field reuse for DDL:
 *   CREATE_TABLE: table = table name, alias = comma-separated member tables,
 *                 ts_start = bucket_seconds (0 → caller uses default)
 *   DROP_TABLE:   table = table name
 *
 * Phase 7 join AST layout:
 *   kind == HM_QUERY_JOIN means a two-pattern MATCH.
 *   Table1/alias1 fields (table, alias, ts_start, ts_end, node_id) hold
 *   the first HYPEREDGE pattern's resolved routing information.
 *   Table2/alias2 fields (table2, alias2, ts_start2, ts_end2, node_id2)
 *   hold the second pattern.
 *   pred_alias[i] records which alias each per-table predicate filters.
 *   Cross-alias join predicates are stored in join_lhs_X/join_rhs_X/join_op.
 */

#ifndef HM_PARSER_H
#define HM_PARSER_H

#include <stdint.h>
#include "lexer.h"

/* ── Query kinds ─────────────────────────────────────────────────────────── */

typedef enum {
    HM_QUERY_SHOW_TABLES   = 0,  /* CALL show_hyperedge_tables()              */
    HM_QUERY_TPI_RANGE     = 1,  /* MATCH ... WHERE ts >= T1 AND ts <= T2     */
    HM_QUERY_FMI_LOOKUP    = 2,  /* MATCH ... WHERE N IN he.members           */
    HM_QUERY_FULL_SCAN     = 3,  /* MATCH ... (no useful WHERE clause)        */
    HM_QUERY_GET_BY_RANGE  = 4,  /* CALL GET_HYPEREDGES_BY_TIME_RANGE(...)    */
    HM_QUERY_PARSE_ERROR   = 5,  /* Syntax / semantic error — see .error      */
    HM_QUERY_CREATE_TABLE  = 6,  /* CREATE HYPEREDGE TABLE ...                */
    HM_QUERY_DROP_TABLE    = 7,  /* DROP HYPEREDGE TABLE <name>               */
    /* Phase 3: DML write statements */
    HM_QUERY_INSERT        = 8,  /* INSERT INTO <table> (cols) VALUES (vals)  */
    HM_QUERY_DELETE        = 9,  /* DELETE FROM <table> WHERE ...             */
    HM_QUERY_UPDATE        = 10, /* UPDATE <table> SET col=val WHERE ...      */
    /* Phase 4: PSI index DDL */
    HM_QUERY_CREATE_INDEX  = 11, /* CREATE INDEX ON <table> (<col>)           */
    HM_QUERY_DROP_INDEX    = 12, /* DROP INDEX ON <table> (<col>)             */
    HM_QUERY_SHOW_INDEXES  = 13, /* CALL show_indexes() / SHOW INDEXES        */
    /* Phase 7: multi-table cross join */
    HM_QUERY_JOIN          = 14, /* MATCH HYPEREDGE (...), HYPEREDGE (...)    */
} HmQueryKind;

#define HM_AST_NAME_LEN   64
#define HM_AST_ERR_LEN   256
#define HM_AST_PROPS_LEN 512   /* raw PROPERTIES(...) body from CREATE TABLE    */

/* ── Phase 2: property predicates ───────────────────────────────────────── */

/*
 * Up to 8 AND-chained property predicates per query, e.g.
 *   AND he.CONFIDENCE > 0.8  AND he.LABEL = 'alpha'
 *
 * Predicates on event_ts and members are handled by ts_start/ts_end and
 * node_id respectively; these slots are for user-defined property columns.
 */
#define HM_MAX_PREDICATES   8
#define HM_AST_VAL_LEN     64   /* max predicate literal length (numeric or text) */
#define HM_AST_RETURN_LEN 256   /* raw RETURN column list, empty = "*"            */
#define HM_AST_ORDER_LEN   64   /* ORDER BY column name, empty = no ordering      */

/* Predicate comparison operators (stored as uint8_t) */
#define HM_PRED_EQ  0   /* =  */
#define HM_PRED_GT  1   /* >  */
#define HM_PRED_GEQ 2   /* >= */
#define HM_PRED_LT  3   /* <  */
#define HM_PRED_LEQ 4   /* <= */

/* ── AST node ────────────────────────────────────────────────────────────── */

typedef struct {
    HmQueryKind kind;

    /* Hyperedge table name and alias extracted from the MATCH / CREATE clause */
    char     table[HM_AST_NAME_LEN];
    char     alias[HM_AST_NAME_LEN];

    /* TPI temporal range (kind == HM_QUERY_TPI_RANGE or HM_QUERY_GET_BY_RANGE) */
    uint32_t ts_start;
    uint32_t ts_end;

    /* FMI member lookup (kind == HM_QUERY_FMI_LOOKUP) */
    uint32_t node_id;

    /* Human-readable parse error (kind == HM_QUERY_PARSE_ERROR) */
    char error[HM_AST_ERR_LEN];

    /*
     * Raw PROPERTIES(...) body from CREATE HYPEREDGE TABLE (V2).
     * e.g. "WEIGHT FLOAT, SEVERITY INT, LABEL TEXT"
     */
    char props_csv[HM_AST_PROPS_LEN];

    /* ── Phase 2: property predicates, RETURN, ORDER BY, LIMIT ───────────── */

    /*
     * Property predicates from additional AND clauses after the time range.
     * Each entry is: col op val, where val is stored as a raw string so
     * Python can coerce it to the correct type from the schema.
     *
     * Example query fragment:
     *   AND he.CONFIDENCE >= 0.8  AND he.LABEL = 'alpha'
     * →  pred_col[0]="CONFIDENCE", pred_op[0]=HM_PRED_GEQ, pred_val[0]="0.8"
     *    pred_col[1]="LABEL",      pred_op[1]=HM_PRED_EQ,  pred_val[1]="alpha",
     *    pred_is_str[1]=1 (string literal, case preserved)
     */
    uint8_t pred_count;
    char    pred_col   [HM_MAX_PREDICATES][HM_AST_NAME_LEN];
    uint8_t pred_op    [HM_MAX_PREDICATES];
    char    pred_val   [HM_MAX_PREDICATES][HM_AST_VAL_LEN];
    uint8_t pred_is_str[HM_MAX_PREDICATES]; /* 1 = string literal, 0 = numeric */

    /*
     * RETURN clause columns (comma-separated, empty = RETURN *).
     * Contains either:
     *   - "COLNAME,COLNAME2"       for simple projections
     *   - "COUNT(*)"               for aggregate count
     *   - "AVG(COLNAME)"           for aggregate avg/max/min/sum
     */
    char return_cols[HM_AST_RETURN_LEN];

    /*
     * ORDER BY: column name (empty = no ordering) and direction flag.
     * 1 = DESC (highest first), 0 = ASC (lowest first).
     */
    char    order_col [HM_AST_ORDER_LEN];
    uint8_t order_desc;

    /* LIMIT N: 0 = no limit */
    uint32_t limit_n;

    /* ── Phase 3: DML (INSERT / DELETE / UPDATE) ─────────────────────────── */

    /*
     * INSERT INTO <table> (<cols>) VALUES (<vals>)
     *
     * insert_cols — comma-separated column names (uppercased).
     *   e.g. "EVENT_TS,MEMBERS,WEIGHT,FORMATION,CONFIDENCE,SEVERITY,LABEL"
     *
     * insert_vals — values for one row, with fields separated by the ASCII
     *   Unit Separator (0x1F).  Member lists are serialised as "[id,id,...]".
     *   e.g. "1000\x1F[1,2]\x1F0.9\x1FWEDGE\x1F0.95\x1F3\x1Falpha"
     *
     * Only the first VALUES tuple is captured (Python splits batches).
     */
#define HM_AST_VALS_LEN  512
#define HM_FIELD_SEP     '\x1F'  /* ASCII Unit Separator */

    char insert_cols[HM_AST_PROPS_LEN]; /* reuse 512-byte size */
    char insert_vals[HM_AST_VALS_LEN];

    /*
     * UPDATE <table> SET col1 = val1, col2 = val2 WHERE ...
     *
     * update_set — field-separated assignments, format identical to
     *   insert_vals but alternating: COL\x1Fval\x1FCOL\x1Fval ...
     *   e.g. "CONFIDENCE\x1F0.99\x1FLABEL\x1Fbeta"
     *
     * The WHERE clause is stored in the shared ts_start/ts_end/predicates
     * fields (same layout as a MATCH ... WHERE query).
     */
#define HM_AST_SET_LEN   512

    char update_set[HM_AST_SET_LEN];

    /* ── Phase 4: PSI index DDL ──────────────────────────────────────────────
     *
     * CREATE INDEX ON <table> (<col>)
     * DROP   INDEX ON <table> (<col>)
     *
     * table     — reuse the existing ast.table field (uppercased)
     * index_col — uppercased column name to index
     *
     * SHOW INDEXES uses ast.table as an optional filter ("" = show all).
     */
    char index_col[HM_AST_NAME_LEN];

    /* ── Phase 7: Multi-table MATCH (Cross-table Joins) ─────────────────────
     *
     * Only populated when kind == HM_QUERY_JOIN.
     *
     * Second HYPEREDGE pattern routing:
     *   table2    — second table name (uppercased)
     *   alias2    — second alias identifier
     *   ts_start2 — resolved lower bound for table2's TPI range (0 = none)
     *   ts_end2   — resolved upper bound for table2's TPI range (UINT32_MAX = none)
     *   node_id2  — node for FMI lookup on table2 (0 = none)
     *
     * Per-predicate alias tracking:
     *   pred_alias[i] — the alias that pred_col[i] / pred_op[i] / pred_val[i]
     *                   belongs to (e.g. "HA" for ha.CONFIDENCE > 0.8).
     *                   Empty string means the predicate is alias-agnostic.
     *
     * Cross-alias join conditions (up to HM_MAX_JOIN_PREDS):
     *   join_count               — number of stored cross-alias predicates
     *   join_lhs_alias[i]        — alias on the left-hand side
     *   join_lhs_col  [i]        — column name on the left-hand side (uppercased)
     *   join_rhs_alias[i]        — alias on the right-hand side
     *   join_rhs_col  [i]        — column name on the right-hand side (uppercased)
     *   join_op       [i]        — comparison operator (HM_PRED_EQ / GT / LT …)
     *
     * Example: ha.event_ts < hb.event_ts
     *   → join_lhs_alias[0]="HA", join_lhs_col[0]="EVENT_TS",
     *     join_rhs_alias[0]="HB", join_rhs_col[0]="EVENT_TS",
     *     join_op[0]=HM_PRED_LT
     *
     * Member-intersection join (42 IN ha.members AND 42 IN hb.members):
     *   node_id  = 42  (routes table1 via FMI)
     *   node_id2 = 42  (routes table2 via FMI)
     */
#define HM_MAX_JOIN_PREDS   4

    char     table2   [HM_AST_NAME_LEN];
    char     alias2   [HM_AST_NAME_LEN];
    uint32_t ts_start2;
    uint32_t ts_end2;
    uint32_t node_id2;

    char     pred_alias[HM_MAX_PREDICATES][HM_AST_NAME_LEN];

    uint8_t  join_count;
    char     join_lhs_alias[HM_MAX_JOIN_PREDS][HM_AST_NAME_LEN];
    char     join_lhs_col  [HM_MAX_JOIN_PREDS][HM_AST_NAME_LEN];
    char     join_rhs_alias[HM_MAX_JOIN_PREDS][HM_AST_NAME_LEN];
    char     join_rhs_col  [HM_MAX_JOIN_PREDS][HM_AST_NAME_LEN];
    uint8_t  join_op       [HM_MAX_JOIN_PREDS];

    /* ── Phase 8: per-table TTL / autocompact threshold ─────────────────────
     *
     * COMPACT_THRESHOLD <n>  in CREATE HYPEREDGE TABLE.
     *
     * When non-zero the Connection applies hm_set_autocompact(store, n) after
     * creating or opening the table.  0 = disabled (default).
     */
    uint32_t compact_threshold;
} HmQueryAst;

/* ── Public API ──────────────────────────────────────────────────────────── */

/*
 * Parse a Cypher-like query string.
 * Returns an HmQueryAst by value; never fails — on syntax error
 * returns an HmQueryAst with kind == HM_QUERY_PARSE_ERROR and a
 * human-readable message in .error.
 */
HmQueryAst hm_parse_query(const char *cypher);

#endif /* HM_PARSER_H */
