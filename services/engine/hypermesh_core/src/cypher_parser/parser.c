/*
 * parser.c — HyperMesh Cypher Recursive-Descent Parser
 */

#include "parser.h"
#include "lexer.h"
#include <ctype.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>

/* ── Internal parser state ───────────────────────────────────────────────── */

typedef struct {
    HmLexer    lex;
    HmQueryAst ast;
    int        had_error;
} Parser;

/* ── Error helpers ───────────────────────────────────────────────────────── */

static void p_error(Parser *p, const char *fmt, ...)
{
    if (p->had_error) return;
    p->had_error  = 1;
    p->ast.kind   = HM_QUERY_PARSE_ERROR;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(p->ast.error, sizeof(p->ast.error), fmt, ap);
    va_end(ap);
}

/* ── Token consumption helpers ───────────────────────────────────────────── */

/* Consume and return if kind matches, else set error and return empty token. */
static HmToken p_expect(Parser *p, HmTokenKind kind, const char *desc)
{
    HmToken t = hm_lexer_peek(&p->lex);
    if (t.kind != kind)
        p_error(p, "Expected %s, got '%s'", desc, t.text[0] ? t.text : "<EOF>");
    return hm_lexer_next(&p->lex);
}

/* Consume and return if it's the given keyword, else error. */
static HmToken p_expect_kw(Parser *p, const char *kw)
{
    HmToken t = hm_lexer_peek(&p->lex);
    if (!hm_tok_is_kw(&t, kw))
        p_error(p, "Expected keyword '%s', got '%s'", kw, t.text[0] ? t.text : "<EOF>");
    return hm_lexer_next(&p->lex);
}

/* Consume and return 1 if the next token is the given keyword, else return 0. */
static int p_try_kw(Parser *p, const char *kw)
{
    if (hm_tok_is_kw(&p->lex.cur, kw)) {
        hm_lexer_next(&p->lex);
        return 1;
    }
    return 0;
}

/* Return 1 if the next token is the given keyword without consuming it. */
static int p_peek_kw(const Parser *p, const char *kw)
{
    return hm_tok_is_kw(&p->lex.cur, kw);
}

/* Discard all remaining tokens. */
static void p_skip_rest(Parser *p)
{
    while (hm_lexer_peek(&p->lex).kind != TOK_EOF)
        hm_lexer_next(&p->lex);
}

/* ── PropRef helper: IDENT.IDENT ─────────────────────────────────────────── */

/*
 * Try to parse "alias.prop_name".
 * Returns 1 on success (fills *alias_out, *prop_out), 0 and restores
 * lexer state on failure.
 */
static int try_propref(Parser *p,
                        char   *alias_out, size_t alias_sz,
                        char   *prop_out,  size_t prop_sz)
{
    HmLexer saved = p->lex;

    HmToken a = hm_lexer_peek(&p->lex);
    if (a.kind != TOK_IDENT) { p->lex = saved; return 0; }
    hm_lexer_next(&p->lex);

    if (hm_lexer_peek(&p->lex).kind != TOK_DOT) { p->lex = saved; return 0; }
    hm_lexer_next(&p->lex); /* consume '.' */

    HmToken prop = hm_lexer_peek(&p->lex);
    if (prop.kind != TOK_IDENT) { p->lex = saved; return 0; }
    hm_lexer_next(&p->lex);

    if (alias_out) { strncpy(alias_out, a.text,    alias_sz - 1); alias_out[alias_sz - 1] = '\0'; }
    if (prop_out)  { strncpy(prop_out,  prop.text, prop_sz  - 1); prop_out[prop_sz  - 1]  = '\0'; }
    return 1;
}

static int is_ts_prop(const char *name)
{
    return strcmp(name, "EVENT_TS") == 0 || strcmp(name, "TIMESTAMP") == 0;
}

static int is_members_prop(const char *name)
{
    return strcmp(name, "MEMBERS")         == 0 ||
           strcmp(name, "MEMBER_NODE_IDS") == 0 ||
           strcmp(name, "MEMBER_IDS")      == 0;
}

/* Is tok a comparison operator? */
static int is_cmp_op(HmTokenKind k)
{
    return k == TOK_GEQ || k == TOK_LEQ || k == TOK_GT || k == TOK_LT;
}

/* ── Phase 2 helpers ─────────────────────────────────────────────────────── */

/*
 * Try to parse a numeric literal: INT  or  INT '.' INT  (float).
 * Writes the decimal string representation into out[outsz].
 * Returns 1 on success, 0 on failure (lexer state unchanged on failure).
 */
static int try_numeric_value(Parser *p, char *out, size_t outsz)
{
    HmToken t = hm_lexer_peek(&p->lex);
    if (t.kind != TOK_INT) return 0;
    hm_lexer_next(&p->lex);

    char buf[HM_AST_VAL_LEN];
    if (hm_lexer_peek(&p->lex).kind == TOK_DOT) {
        HmLexer saved_dot = p->lex;
        hm_lexer_next(&p->lex);
        HmToken frac = hm_lexer_peek(&p->lex);
        if (frac.kind == TOK_INT) {
            hm_lexer_next(&p->lex);
            snprintf(buf, sizeof(buf), "%u.%s", t.ival,
                     frac.text[0] ? frac.text : "0");
        } else {
            p->lex = saved_dot;
            snprintf(buf, sizeof(buf), "%u", t.ival);
        }
    } else {
        snprintf(buf, sizeof(buf), "%u", t.ival);
    }
    strncpy(out, buf, outsz - 1);
    out[outsz - 1] = '\0';
    return 1;
}

/*
 * Store one property predicate that has already been fully parsed.
 *
 * alias — which query alias this predicate belongs to (e.g. "HA").
 *         Pass NULL or "" for single-table queries (pred_alias[i] left empty).
 */
static void store_prop_pred_alias(Parser      *p,
                                   const char  *col,
                                   HmTokenKind  opkind,
                                   const char  *val_str,
                                   uint8_t      is_str,
                                   const char  *alias)
{
    if (p->ast.pred_count >= HM_MAX_PREDICATES) return;
    int i = (int)p->ast.pred_count++;
    strncpy(p->ast.pred_col[i], col, HM_AST_NAME_LEN - 1);
    p->ast.pred_col[i][HM_AST_NAME_LEN - 1] = '\0';
    switch (opkind) {
        case TOK_EQ:  p->ast.pred_op[i] = HM_PRED_EQ;  break;
        case TOK_GT:  p->ast.pred_op[i] = HM_PRED_GT;  break;
        case TOK_GEQ: p->ast.pred_op[i] = HM_PRED_GEQ; break;
        case TOK_LT:  p->ast.pred_op[i] = HM_PRED_LT;  break;
        case TOK_LEQ: p->ast.pred_op[i] = HM_PRED_LEQ; break;
        default:      p->ast.pred_op[i] = HM_PRED_EQ;
    }
    strncpy(p->ast.pred_val[i], val_str, HM_AST_VAL_LEN - 1);
    p->ast.pred_val[i][HM_AST_VAL_LEN - 1] = '\0';
    p->ast.pred_is_str[i] = is_str;
    if (alias && *alias) {
        strncpy(p->ast.pred_alias[i], alias, HM_AST_NAME_LEN - 1);
        p->ast.pred_alias[i][HM_AST_NAME_LEN - 1] = '\0';
    } else {
        p->ast.pred_alias[i][0] = '\0';
    }
}

/* Convenience wrapper — no alias tracking (single-table queries). */
static void store_prop_pred(Parser      *p,
                             const char  *col,
                             HmTokenKind  opkind,
                             const char  *val_str,
                             uint8_t      is_str)
{
    store_prop_pred_alias(p, col, opkind, val_str, is_str, NULL);
}

/*
 * Greedily consume  AND alias.PROP op VALUE  clauses.
 * Called after the primary WHERE predicate (time range or FMI) is parsed.
 * alias_ctx — the expected alias for single-table queries (may be NULL).
 *             In single-table mode ts/members predicates halt the loop;
 *             in join mode (alias_ctx == NULL) ts/members are delegated
 *             to the caller.
 */
static void parse_prop_predicates_ctx(Parser *p, const char *alias_ctx)
{
    while (p_peek_kw(p, "AND") && p->ast.pred_count < HM_MAX_PREDICATES) {
        HmLexer saved = p->lex;
        hm_lexer_next(&p->lex);

        char alias[HM_AST_NAME_LEN] = {0}, col[HM_AST_NAME_LEN] = {0};
        if (!try_propref(p, alias, sizeof(alias), col, sizeof(col))) {
            p->lex = saved;
            break;
        }

        if (is_ts_prop(col) || is_members_prop(col)) {
            p->lex = saved;
            break;
        }

        HmToken op_tok = hm_lexer_peek(&p->lex);
        if (!is_cmp_op(op_tok.kind) && op_tok.kind != TOK_EQ) {
            p->lex = saved;
            break;
        }
        HmTokenKind opkind = op_tok.kind;
        hm_lexer_next(&p->lex);

        HmToken val_tok = hm_lexer_peek(&p->lex);
        char    val_str[HM_AST_VAL_LEN] = {0};
        uint8_t is_str = 0;

        if (val_tok.kind == TOK_STRING) {
            strncpy(val_str, val_tok.text, HM_AST_VAL_LEN - 1);
            is_str = 1;
            hm_lexer_next(&p->lex);
        } else if (!try_numeric_value(p, val_str, HM_AST_VAL_LEN)) {
            p->lex = saved;
            break;
        }

        /* Use the explicit alias from the query; fall back to alias_ctx. */
        const char *eff_alias = (alias[0] != '\0') ? alias : (alias_ctx ? alias_ctx : "");
        store_prop_pred_alias(p, col, opkind, val_str, is_str, eff_alias);
    }
}

/* Single-table convenience wrapper (no alias tracking). */
static void parse_prop_predicates(Parser *p)
{
    parse_prop_predicates_ctx(p, NULL);
}

/* ── Phase 7: Join helper — store a cross-alias join predicate ──────────── */

static void store_join_pred(Parser      *p,
                             const char  *lhs_alias, const char *lhs_col,
                             HmTokenKind  opkind,
                             const char  *rhs_alias, const char *rhs_col)
{
    if (p->ast.join_count >= HM_MAX_JOIN_PREDS) return;
    int j = (int)p->ast.join_count++;

    strncpy(p->ast.join_lhs_alias[j], lhs_alias, HM_AST_NAME_LEN - 1);
    p->ast.join_lhs_alias[j][HM_AST_NAME_LEN - 1] = '\0';
    strncpy(p->ast.join_lhs_col  [j], lhs_col,   HM_AST_NAME_LEN - 1);
    p->ast.join_lhs_col  [j][HM_AST_NAME_LEN - 1] = '\0';
    strncpy(p->ast.join_rhs_alias[j], rhs_alias, HM_AST_NAME_LEN - 1);
    p->ast.join_rhs_alias[j][HM_AST_NAME_LEN - 1] = '\0';
    strncpy(p->ast.join_rhs_col  [j], rhs_col,   HM_AST_NAME_LEN - 1);
    p->ast.join_rhs_col  [j][HM_AST_NAME_LEN - 1] = '\0';
    switch (opkind) {
        case TOK_EQ:  p->ast.join_op[j] = HM_PRED_EQ;  break;
        case TOK_GT:  p->ast.join_op[j] = HM_PRED_GT;  break;
        case TOK_GEQ: p->ast.join_op[j] = HM_PRED_GEQ; break;
        case TOK_LT:  p->ast.join_op[j] = HM_PRED_LT;  break;
        case TOK_LEQ: p->ast.join_op[j] = HM_PRED_LEQ; break;
        default:      p->ast.join_op[j] = HM_PRED_EQ;
    }
}

/*
 * Resolve a TPI range bound into the correct table's ts_start/ts_end slot.
 * alias1/alias2 are the uppercased alias strings for the two patterns.
 */
static void apply_ts_bound(Parser     *p,
                            const char *which_alias,
                            const char *alias1,
                            const char *alias2,
                            HmTokenKind op,
                            uint32_t    val)
{
    int is1 = (strcmp(which_alias, alias1) == 0);
    int is2 = (alias2 && strcmp(which_alias, alias2) == 0);

    uint32_t *ts_start_ptr = is2 ? &p->ast.ts_start2 : &p->ast.ts_start;
    uint32_t *ts_end_ptr   = is2 ? &p->ast.ts_end2   : &p->ast.ts_end;

    (void)is1; /* used for clarity only */

    if      (op == TOK_GEQ) { if (*ts_start_ptr == 0 || val     > *ts_start_ptr) *ts_start_ptr = val;     }
    else if (op == TOK_GT)  { if (*ts_start_ptr == 0 || val + 1 > *ts_start_ptr) *ts_start_ptr = val + 1; }
    else if (op == TOK_LEQ) { if (*ts_end_ptr == UINT32_MAX || val     < *ts_end_ptr) *ts_end_ptr = val;     }
    else if (op == TOK_LT)  { if (*ts_end_ptr == UINT32_MAX || val - 1 < *ts_end_ptr) *ts_end_ptr = (val > 0 ? val - 1 : 0); }
}

/*
 * Parse the WHERE body for a two-pattern JOIN query.
 *
 * Handles:
 *   (a)  alias1.event_ts OP Int        → TPI bound on table1
 *   (b)  alias2.event_ts OP Int        → TPI bound on table2
 *   (c)  alias1.PROP OP alias2.PROP    → cross-alias join predicate
 *   (d)  alias1.PROP OP Value          → per-table property predicate
 *   (e)  Int IN alias.members          → FMI lookup on that table
 *
 * All of the above may be chained with AND.
 * The kind is unconditionally set to HM_QUERY_JOIN by the caller.
 */
static void parse_join_where(Parser     *p,
                              const char *alias1, const char *alias2)
{
    /* Initialise table2 routing sentinels. */
    p->ast.ts_start2 = 0;
    p->ast.ts_end2   = UINT32_MAX;
    p->ast.node_id2  = 0;

    /* Same for table1 (may have been zeroed by memset already). */
    p->ast.ts_start = 0;
    p->ast.ts_end   = UINT32_MAX;
    p->ast.node_id  = 0;

    int first = 1;

    while (!p->had_error) {
        if (!first) {
            if (!p_peek_kw(p, "AND")) break;
            hm_lexer_next(&p->lex); /* consume AND */
        }
        first = 0;

        /* ── Try: Int IN alias.members ─── */
        if (hm_lexer_peek(&p->lex).kind == TOK_INT) {
            HmLexer saved = p->lex;
            uint32_t candidate = hm_lexer_next(&p->lex).ival;
            if (p_peek_kw(p, "IN")) {
                hm_lexer_next(&p->lex);
                char pa[HM_AST_NAME_LEN] = {0}, pn[HM_AST_NAME_LEN] = {0};
                if (try_propref(p, pa, sizeof(pa), pn, sizeof(pn)) && is_members_prop(pn)) {
                    if (strcmp(pa, alias2) == 0) {
                        p->ast.node_id2 = candidate;
                    } else {
                        p->ast.node_id = candidate;
                    }
                    continue;
                }
            }
            p->lex = saved;
            /* Not an IN-members pattern; fall through to propref handling. */
        }

        /* ── Try: alias.prop OP ... ─── */
        HmLexer saved_start = p->lex;
        char lhs_a[HM_AST_NAME_LEN] = {0}, lhs_p[HM_AST_NAME_LEN] = {0};
        if (!try_propref(p, lhs_a, sizeof(lhs_a), lhs_p, sizeof(lhs_p))) {
            p->lex = saved_start;
            break;
        }

        HmToken op_tok = hm_lexer_peek(&p->lex);
        if (!is_cmp_op(op_tok.kind) && op_tok.kind != TOK_EQ) {
            p->lex = saved_start;
            break;
        }
        HmTokenKind opkind = op_tok.kind;
        hm_lexer_next(&p->lex);

        /* ── Peek right-hand side: another alias.prop or a literal? ─── */
        HmLexer saved_rhs = p->lex;
        char rhs_a[HM_AST_NAME_LEN] = {0}, rhs_p[HM_AST_NAME_LEN] = {0};
        if (try_propref(p, rhs_a, sizeof(rhs_a), rhs_p, sizeof(rhs_p))) {
            /* Cross-alias join condition: lhs_a.lhs_p OP rhs_a.rhs_p */
            store_join_pred(p, lhs_a, lhs_p, opkind, rhs_a, rhs_p);
            continue;
        }
        p->lex = saved_rhs;

        /* ── Scalar literal on the RHS ─── */
        if (is_ts_prop(lhs_p)) {
            /* Temporal bound for whichever alias this is */
            if (!is_cmp_op(opkind)) { p->lex = saved_start; break; }
            HmToken vt = hm_lexer_peek(&p->lex);
            if (vt.kind != TOK_INT) { p->lex = saved_start; break; }
            hm_lexer_next(&p->lex);
            apply_ts_bound(p, lhs_a, alias1, alias2, opkind, vt.ival);
            continue;
        }

        if (is_members_prop(lhs_p)) {
            /* members predicate not supported as LHS in join WHERE */
            p->lex = saved_start;
            break;
        }

        /* Regular per-table property predicate */
        char    val_str[HM_AST_VAL_LEN] = {0};
        uint8_t is_str = 0;
        HmToken val_tok = hm_lexer_peek(&p->lex);
        if (val_tok.kind == TOK_STRING) {
            strncpy(val_str, val_tok.text, HM_AST_VAL_LEN - 1);
            is_str = 1;
            hm_lexer_next(&p->lex);
        } else if (!try_numeric_value(p, val_str, HM_AST_VAL_LEN)) {
            p->lex = saved_start;
            break;
        }
        store_prop_pred_alias(p, lhs_p, opkind, val_str, is_str, lhs_a);
    }
}

/*
 * Parse the RETURN clause (RETURN keyword already consumed).
 * Handles: RETURN *   |   RETURN he.COL, COUNT(*), AVG(he.COL) ...
 */
static void parse_return(Parser *p)
{
    if (hm_lexer_peek(&p->lex).kind == TOK_STAR) {
        hm_lexer_next(&p->lex);
        return;
    }

    size_t pos   = 0;
    int    first = 1;

    while (!p_peek_kw(p, "ORDER") && !p_peek_kw(p, "LIMIT") &&
           !p_peek_kw(p, "SKIP")  &&
           hm_lexer_peek(&p->lex).kind != TOK_EOF) {

        if (!first) {
            if (hm_lexer_peek(&p->lex).kind == TOK_COMMA)
                hm_lexer_next(&p->lex);
            else
                break;
        }
        first = 0;

        char   item[HM_AST_RETURN_LEN] = {0};
        HmToken t = hm_lexer_peek(&p->lex);

        if (t.kind != TOK_IDENT) break;

        char ident[HM_AST_NAME_LEN];
        strncpy(ident, t.text, HM_AST_NAME_LEN - 1);
        hm_lexer_next(&p->lex);

        if (hm_lexer_peek(&p->lex).kind == TOK_LPAREN) {
            /* Aggregate: FN(inner) */
            hm_lexer_next(&p->lex);
            char inner[HM_AST_NAME_LEN * 2] = {0};
            size_t in_pos = 0;
            int depth = 1;
            while (depth > 0 && hm_lexer_peek(&p->lex).kind != TOK_EOF) {
                HmToken it = hm_lexer_next(&p->lex);
                if      (it.kind == TOK_LPAREN)  { depth++; }
                else if (it.kind == TOK_RPAREN)  { depth--; if (depth == 0) break; }
                if (depth > 0 && in_pos < sizeof(inner) - 2) {
                    if      (it.kind == TOK_STAR) { inner[in_pos++] = '*'; }
                    else if (it.kind == TOK_DOT)  { inner[in_pos++] = '.'; }
                    else {
                        size_t tl = strlen(it.text);
                        if (in_pos + tl < sizeof(inner) - 1) {
                            memcpy(&inner[in_pos], it.text, tl);
                            in_pos += tl;
                        }
                    }
                    inner[in_pos] = '\0';
                }
            }
            snprintf(item, sizeof(item), "%s(%s)", ident, inner);
        } else if (hm_lexer_peek(&p->lex).kind == TOK_DOT) {
            /* alias.col — preserve the full "ALIAS.COL" string so that join
             * queries can route to the correct prefixed merged column.      */
            hm_lexer_next(&p->lex);
            HmToken col_tok = hm_lexer_peek(&p->lex);
            if (col_tok.kind == TOK_IDENT) {
                hm_lexer_next(&p->lex);
                snprintf(item, sizeof(item), "%s.%s", ident, col_tok.text);
            } else {
                strncpy(item, ident, sizeof(item) - 1);
            }
        } else {
            strncpy(item, ident, sizeof(item) - 1);
        }

        size_t item_len = strlen(item);
        if (item_len == 0) break;

        if (pos > 0 && pos < sizeof(p->ast.return_cols) - 2)
            p->ast.return_cols[pos++] = ',';
        if (pos + item_len < sizeof(p->ast.return_cols) - 1) {
            memcpy(&p->ast.return_cols[pos], item, item_len);
            pos += item_len;
        }
        p->ast.return_cols[pos] = '\0';
    }
}

/*
 * Parse  ORDER BY (alias.col | ident) [ASC | DESC].
 * ORDER keyword has NOT yet been consumed when this is called.
 *
 * The full alias.col string is preserved in ast.order_col (e.g. "ha.EVENT_TS")
 * so that join queries can route to the correct merged column.  Single-table
 * queries can still strip the alias on the Python side.
 */
static void parse_order_by(Parser *p)
{
    hm_lexer_next(&p->lex);  /* consume ORDER */
    if (!p_peek_kw(p, "BY")) return;
    hm_lexer_next(&p->lex);  /* consume BY */

    char alias[HM_AST_NAME_LEN] = {0}, col[HM_AST_NAME_LEN] = {0};
    if (try_propref(p, alias, sizeof(alias), col, sizeof(col))) {
        /* Preserve "alias.col" so join queries can resolve the right column. */
        if (alias[0] != '\0') {
            snprintf(p->ast.order_col, HM_AST_ORDER_LEN, "%s.%s", alias, col);
        } else {
            strncpy(p->ast.order_col, col, HM_AST_ORDER_LEN - 1);
        }
        p->ast.order_col[HM_AST_ORDER_LEN - 1] = '\0';
    } else {
        HmToken t = hm_lexer_peek(&p->lex);
        if (t.kind == TOK_IDENT) {
            strncpy(p->ast.order_col, t.text, HM_AST_ORDER_LEN - 1);
            hm_lexer_next(&p->lex);
        }
    }

    if      (p_peek_kw(p, "DESC")) { p->ast.order_desc = 1; hm_lexer_next(&p->lex); }
    else if (p_peek_kw(p, "ASC"))  {                         hm_lexer_next(&p->lex); }
}

/* ── WHERE clause parser ─────────────────────────────────────────────────── */

/*
 * Parse the WHERE body (the keyword WHERE has already been consumed).
 *
 * Primary recognised forms:
 *   (a)  he.event_ts OP Int  [ AND  he.event_ts OP Int ]  → TPI_RANGE
 *   (b)  Int IN he.members                                  → FMI_LOOKUP
 *   (c)  he.PROP op (Int|Float|String)  (no ts bound)      → FULL_SCAN
 *
 * After the primary predicate, additional  AND he.PROP op VALUE  clauses
 * are captured in ast.pred_col / pred_op / pred_val via parse_prop_predicates.
 */
static void parse_where(Parser *p)
{
    /* ── (b) Integer IN he.members ───────────────────────────────────────── */
    if (hm_lexer_peek(&p->lex).kind == TOK_INT) {
        HmLexer saved = p->lex;
        uint32_t candidate = hm_lexer_next(&p->lex).ival;

        if (p_peek_kw(p, "IN")) {
            hm_lexer_next(&p->lex); /* consume IN */
            char pa[HM_AST_NAME_LEN], pn[HM_AST_NAME_LEN];
            memset(pa, 0, sizeof(pa)); memset(pn, 0, sizeof(pn));
            if (try_propref(p, pa, sizeof(pa), pn, sizeof(pn)) && is_members_prop(pn)) {
                p->ast.kind    = HM_QUERY_FMI_LOOKUP;
                p->ast.node_id = candidate;
                parse_prop_predicates(p);
                return;
            }
        }
        p->lex = saved; /* backtrack — not a recognised IN-pattern */
    }

    /* ── Attempt (a): he.ts OP Int  [AND  he.ts OP Int]  → TPI_RANGE ─────── */
    char pa1[HM_AST_NAME_LEN] = {0}, pn1[HM_AST_NAME_LEN] = {0};
    if (!try_propref(p, pa1, sizeof(pa1), pn1, sizeof(pn1))) {
        p->ast.kind = HM_QUERY_FULL_SCAN;
        return;
    }

    HmToken op1 = hm_lexer_peek(&p->lex);
    int op1_is_cmp = is_cmp_op(op1.kind);
    int op1_is_eq  = (op1.kind == TOK_EQ);

    if (!op1_is_cmp && !op1_is_eq) {
        p->ast.kind = HM_QUERY_FULL_SCAN;
        return;
    }
    hm_lexer_next(&p->lex); /* consume operator */

    if (!is_ts_prop(pn1)) {
        /*
         * ── (c) Non-timestamp property as the first WHERE predicate ────────
         * We have: pn1 op1 VALUE.  Capture this as a predicate, set
         * kind = FULL_SCAN, then continue eating additional AND predicates.
         */
        p->ast.kind = HM_QUERY_FULL_SCAN;

        char val_str[HM_AST_VAL_LEN] = {0};
        uint8_t is_str = 0;
        HmToken vt = hm_lexer_peek(&p->lex);
        if (vt.kind == TOK_STRING) {
            strncpy(val_str, vt.text, HM_AST_VAL_LEN - 1);
            is_str = 1;
            hm_lexer_next(&p->lex);
            store_prop_pred(p, pn1, op1.kind, val_str, is_str);
        } else if (try_numeric_value(p, val_str, HM_AST_VAL_LEN)) {
            store_prop_pred(p, pn1, op1.kind, val_str, 0);
        }
        /* Either way, continue scanning for more AND predicates */
        parse_prop_predicates(p);
        return;
    }

    /* ts property: op must be a comparison (not =) */
    if (!op1_is_cmp) {
        p->ast.kind = HM_QUERY_FULL_SCAN;
        return;
    }

    /* Value must be an integer for the time range */
    HmToken val1 = hm_lexer_peek(&p->lex);
    if (val1.kind != TOK_INT) {
        p->ast.kind = HM_QUERY_FULL_SCAN;
        return;
    }
    hm_lexer_next(&p->lex);

    /*
     * First half of the range is now in (op1, val1).
     *   he.ts >= v1  → lhs = v1,    rhs = UINT32_MAX
     *   he.ts >  v1  → lhs = v1+1,  rhs = UINT32_MAX
     *   he.ts <= v1  → lhs = 0,     rhs = v1
     *   he.ts <  v1  → lhs = 0,     rhs = v1-1
     */
    uint32_t lhs, rhs;
    if      (op1.kind == TOK_GEQ) { lhs = val1.ival;     rhs = UINT32_MAX; }
    else if (op1.kind == TOK_GT)  { lhs = val1.ival + 1; rhs = UINT32_MAX; }
    else if (op1.kind == TOK_LEQ) { lhs = 0;             rhs = val1.ival;  }
    else /* TOK_LT */             { lhs = 0;             rhs = val1.ival > 0 ? val1.ival - 1 : 0; }

    /* Optional AND second half of time range */
    if (p_peek_kw(p, "AND")) {
        HmLexer saved_and = p->lex;
        hm_lexer_next(&p->lex); /* tentatively consume AND */

        char pa2[HM_AST_NAME_LEN] = {0}, pn2[HM_AST_NAME_LEN] = {0};
        if (try_propref(p, pa2, sizeof(pa2), pn2, sizeof(pn2)) && is_ts_prop(pn2)) {
            HmToken op2 = hm_lexer_peek(&p->lex);
            if (is_cmp_op(op2.kind)) {
                hm_lexer_next(&p->lex);
                HmToken val2 = hm_lexer_peek(&p->lex);
                if (val2.kind == TOK_INT) {
                    hm_lexer_next(&p->lex);
                    uint32_t v2 = val2.ival;
                    if      (op2.kind == TOK_GEQ) lhs = v2;
                    else if (op2.kind == TOK_GT)  lhs = v2 + 1;
                    else if (op2.kind == TOK_LEQ) rhs = v2;
                    else /* LT */                 rhs = v2 > 0 ? v2 - 1 : 0;
                } else {
                    p->lex = saved_and; /* can't parse second half — backtrack */
                }
            } else {
                p->lex = saved_and;
            }
        } else {
            /* AND is followed by a property predicate, not a ts bound — backtrack */
            p->lex = saved_and;
        }
    }

    /* Normalise order (handle ts <= T2 AND ts >= T1) */
    if (lhs > rhs) { uint32_t tmp = lhs; lhs = rhs; rhs = tmp; }

    p->ast.kind     = HM_QUERY_TPI_RANGE;
    p->ast.ts_start = lhs;
    p->ast.ts_end   = rhs;

    /* Consume any trailing AND property predicates */
    parse_prop_predicates(p);
}

/* ── Shared helper: parse  ( alias : TableName ) ────────────────────────── */

static void parse_hyperedge_pattern(Parser *p,
                                     char   *alias_out, size_t alias_sz,
                                     char   *table_out, size_t table_sz)
{
    p_expect(p, TOK_LPAREN, "'('");
    if (p->had_error) return;

    HmToken alias_tok = hm_lexer_peek(&p->lex);
    if (alias_tok.kind != TOK_IDENT) {
        p_error(p, "Expected alias identifier in MATCH HYPEREDGE (alias:Table)");
        return;
    }
    strncpy(alias_out, alias_tok.text, alias_sz - 1);
    alias_out[alias_sz - 1] = '\0';
    hm_lexer_next(&p->lex);

    p_expect(p, TOK_COLON, "':' after alias");
    if (p->had_error) return;

    HmToken table_tok = hm_lexer_peek(&p->lex);
    if (table_tok.kind != TOK_IDENT) {
        p_error(p, "Expected table name after ':' in MATCH HYPEREDGE");
        return;
    }
    strncpy(table_out, table_tok.text, table_sz - 1);
    table_out[table_sz - 1] = '\0';
    hm_lexer_next(&p->lex);

    p_expect(p, TOK_RPAREN, "')'");
}

/* ── MATCH HYPEREDGE parser ─────────────────────────────────────────────── */

static void parse_match(Parser *p)
{
    /* MATCH already consumed. */
    p_expect_kw(p, "HYPEREDGE");
    if (p->had_error) return;

    /* Parse first  ( alias : TableName ) */
    parse_hyperedge_pattern(p,
                             p->ast.alias, sizeof(p->ast.alias),
                             p->ast.table, sizeof(p->ast.table));
    if (p->had_error) return;

    /* ── Check for a second HYPEREDGE pattern (Phase 7 join) ───────────────
     * Syntax: MATCH HYPEREDGE (ha:A), HYPEREDGE (hb:B) WHERE ...
     */
    if (hm_lexer_peek(&p->lex).kind == TOK_COMMA) {
        hm_lexer_next(&p->lex); /* consume ',' */

        if (!p_peek_kw(p, "HYPEREDGE")) {
            p_error(p, "Expected 'HYPEREDGE' after ',' in multi-pattern MATCH");
            return;
        }
        hm_lexer_next(&p->lex); /* consume HYPEREDGE */

        parse_hyperedge_pattern(p,
                                 p->ast.alias2, sizeof(p->ast.alias2),
                                 p->ast.table2, sizeof(p->ast.table2));
        if (p->had_error) return;

        /* Parse optional WHERE with join semantics */
        if (p_try_kw(p, "WHERE")) {
            parse_join_where(p, p->ast.alias, p->ast.alias2);
        } else {
            /* No WHERE — cross join: initialise routing sentinels */
            p->ast.ts_start2 = 0;
            p->ast.ts_end2   = UINT32_MAX;
            p->ast.ts_start  = 0;
            p->ast.ts_end    = UINT32_MAX;
        }
        /* Unconditionally mark as JOIN — Python execution engine decides
         * sub-strategy (temporal overlap / member intersection / etc.)    */
        p->ast.kind = HM_QUERY_JOIN;

        /* Optional RETURN, ORDER BY, LIMIT (shared with single-table path) */
        memset(p->ast.return_cols, 0, sizeof(p->ast.return_cols));
        if (p_peek_kw(p, "RETURN")) { hm_lexer_next(&p->lex); parse_return(p); }
        memset(p->ast.order_col, 0, sizeof(p->ast.order_col));
        p->ast.order_desc = 0;
        if (p_peek_kw(p, "ORDER")) { parse_order_by(p); }
        p->ast.limit_n = 0;
        if (p_peek_kw(p, "LIMIT")) {
            hm_lexer_next(&p->lex);
            HmToken n = hm_lexer_peek(&p->lex);
            if (n.kind == TOK_INT) { p->ast.limit_n = n.ival; hm_lexer_next(&p->lex); }
        }
        p_skip_rest(p);
        return;
    }

    /* ── Single-table path (original behaviour) ──────────────────────────── */

    /* Optional  MEMBERS [ MemberSpec , ... ]
     * We parse and discard the member specs — they don't affect routing.    */
    if (p_peek_kw(p, "MEMBERS")) {
        hm_lexer_next(&p->lex); /* consume MEMBERS */
        if (hm_lexer_peek(&p->lex).kind == TOK_LBRACKET) {
            hm_lexer_next(&p->lex); /* consume [ */
            int depth = 1;
            while (depth > 0 && hm_lexer_peek(&p->lex).kind != TOK_EOF) {
                HmTokenKind k = hm_lexer_next(&p->lex).kind;
                if      (k == TOK_LBRACKET) depth++;
                else if (k == TOK_RBRACKET) depth--;
            }
        }
    }

    /* Optional WHERE */
    if (p_try_kw(p, "WHERE")) {
        parse_where(p);
    } else {
        p->ast.kind = HM_QUERY_FULL_SCAN;
    }

    /* Optional RETURN */
    memset(p->ast.return_cols, 0, sizeof(p->ast.return_cols));
    if (p_peek_kw(p, "RETURN")) {
        hm_lexer_next(&p->lex);
        parse_return(p);
    }

    /* Optional ORDER BY */
    memset(p->ast.order_col, 0, sizeof(p->ast.order_col));
    p->ast.order_desc = 0;
    if (p_peek_kw(p, "ORDER")) {
        parse_order_by(p);
    }

    /* Optional LIMIT */
    p->ast.limit_n = 0;
    if (p_peek_kw(p, "LIMIT")) {
        hm_lexer_next(&p->lex);
        HmToken n = hm_lexer_peek(&p->lex);
        if (n.kind == TOK_INT) {
            p->ast.limit_n = n.ival;
            hm_lexer_next(&p->lex);
        }
    }

    p_skip_rest(p); /* discard any remaining tokens */
}

/* ── CALL statement parsers ─────────────────────────────────────────────── */

/* CALL SHOW_HYPEREDGE_TABLES ( ) */
static void parse_show_tables(Parser *p)
{
    /* CALL and the SHOW_HYPEREDGE_TABLE(S) keyword are already consumed
     * by the caller; start here from the opening parenthesis.           */
    p_expect(p, TOK_LPAREN, "'('");
    if (p->had_error) return;
    p_expect(p, TOK_RPAREN, "')'");
    if (p->had_error) return;
    p->ast.kind = HM_QUERY_SHOW_TABLES;
    p_skip_rest(p);
}

/* CALL GET_HYPEREDGES_BY_TIME_RANGE ( TableRef, T1, T2 ) */
static void parse_get_by_range(Parser *p)
{
    /* CALL and GET_HYPEREDGES_BY_TIME_RANGE already consumed. */
    p_expect(p, TOK_LPAREN, "'(' after GET_HYPEREDGES_BY_TIME_RANGE");
    if (p->had_error) return;

    /* Table name: string literal or bare identifier.
     * Upper-case the result so table names are case-insensitive.         */
    HmToken tbl = hm_lexer_peek(&p->lex);
    if (tbl.kind == TOK_STRING || tbl.kind == TOK_IDENT) {
        strncpy(p->ast.table, tbl.text, sizeof(p->ast.table) - 1);
        hm_lexer_next(&p->lex);
        /* Identifiers come pre-upper-cased from the lexer; strings do not. */
        if (tbl.kind == TOK_STRING) {
            for (int i = 0; i < (int)sizeof(p->ast.table) - 1 && p->ast.table[i]; i++)
                p->ast.table[i] = (char)toupper((unsigned char)p->ast.table[i]);
        }
    } else {
        p_error(p, "Expected table name in GET_HYPEREDGES_BY_TIME_RANGE");
        return;
    }

    p_expect(p, TOK_COMMA, "','");  if (p->had_error) return;
    HmToken t1 = p_expect(p, TOK_INT, "start timestamp"); if (p->had_error) return;
    p_expect(p, TOK_COMMA, "','");  if (p->had_error) return;
    HmToken t2 = p_expect(p, TOK_INT, "end timestamp");   if (p->had_error) return;
    p_expect(p, TOK_RPAREN, "')'"); if (p->had_error) return;

    p->ast.ts_start = t1.ival;
    p->ast.ts_end   = t2.ival;
    p->ast.kind     = HM_QUERY_GET_BY_RANGE;
    p_skip_rest(p);
}

/* ── DDL parsers ─────────────────────────────────────────────────────────── */

/*
 * CREATE HYPEREDGE TABLE <name> ( <member1>, <member2>, ... )
 *     [ PROPERTIES ( <prop> <type>, ... ) ]
 *     [ BUCKET_SECONDS <n> ]
 *
 * Fills:
 *   ast.table     = table name (uppercased by lexer)
 *   ast.alias     = comma-separated member table names  e.g. "DRONE,DRONE"
 *   ast.ts_start  = bucket_seconds (0 → caller uses its own default)
 *   ast.kind      = HM_QUERY_CREATE_TABLE
 */
static void parse_create_hyperedge_table(Parser *p)
{
    /* CREATE already consumed. */
    p_expect_kw(p, "HYPEREDGE"); if (p->had_error) return;
    p_expect_kw(p, "TABLE");     if (p->had_error) return;

    HmToken name_tok = p_expect(p, TOK_IDENT, "table name");
    if (p->had_error) return;
    strncpy(p->ast.table, name_tok.text, sizeof(p->ast.table) - 1);

    /* Member table list: ( Name, Name, ... ) */
    p_expect(p, TOK_LPAREN, "'(' after table name"); if (p->had_error) return;

    memset(p->ast.alias, 0, sizeof(p->ast.alias));
    size_t alias_pos = 0;
    int is_first = 1;

    while (!p->had_error) {
        HmToken t = hm_lexer_peek(&p->lex);
        if (t.kind == TOK_RPAREN || t.kind == TOK_EOF) break;

        if (!is_first) {
            if (t.kind != TOK_COMMA) {
                p_error(p, "Expected ',' or ')' in member table list, got '%s'", t.text);
                return;
            }
            hm_lexer_next(&p->lex);
            t = hm_lexer_peek(&p->lex);
        }

        if (t.kind != TOK_IDENT) {
            p_error(p, "Expected member table name, got '%s'", t.text);
            return;
        }
        hm_lexer_next(&p->lex);

        if (!is_first && alias_pos < sizeof(p->ast.alias) - 2)
            p->ast.alias[alias_pos++] = ',';

        size_t tlen = strlen(t.text);
        size_t space = sizeof(p->ast.alias) - 1 - alias_pos;
        if (tlen > space) tlen = space;
        memcpy(&p->ast.alias[alias_pos], t.text, tlen);
        alias_pos += tlen;
        is_first = 0;
    }

    p_expect(p, TOK_RPAREN, "')'"); if (p->had_error) return;

    /* Optional PROPERTIES (...) — capture raw body into ast.props_csv. */
    memset(p->ast.props_csv, 0, sizeof(p->ast.props_csv));
    if (p_peek_kw(p, "PROPERTIES")) {
        hm_lexer_next(&p->lex);
        if (hm_lexer_peek(&p->lex).kind == TOK_LPAREN) {
            hm_lexer_next(&p->lex); /* consume '(' */
            int depth = 1;
            size_t pos = 0;
            int first_tok = 1;
            while (depth > 0 && hm_lexer_peek(&p->lex).kind != TOK_EOF) {
                HmToken tok = hm_lexer_next(&p->lex);
                if (tok.kind == TOK_LPAREN) {
                    depth++;
                } else if (tok.kind == TOK_RPAREN) {
                    depth--;
                    if (depth == 0) break; /* closing ')' of PROPERTIES */
                }
                if (depth > 0) {
                    if (tok.kind == TOK_COMMA) {
                        /* Write literal comma + space as the column separator */
                        if (pos < sizeof(p->ast.props_csv) - 3) {
                            p->ast.props_csv[pos++] = ',';
                            p->ast.props_csv[pos++] = ' ';
                        }
                    } else {
                        /* Non-comma token: add a space separator (except first) */
                        if (!first_tok) {
                            if (pos < sizeof(p->ast.props_csv) - 2)
                                p->ast.props_csv[pos++] = ' ';
                        }
                        first_tok = 0;
                        size_t tlen = strlen(tok.text);
                        if (pos + tlen < sizeof(p->ast.props_csv) - 1) {
                            memcpy(&p->ast.props_csv[pos], tok.text, tlen);
                            pos += tlen;
                        }
                    }
                }
            }
            p->ast.props_csv[pos] = '\0';
        }
    }

    /* Optional BUCKET_SECONDS <n>. */
    p->ast.ts_start = 0;
    if (p_peek_kw(p, "BUCKET_SECONDS")) {
        hm_lexer_next(&p->lex);
        HmToken bs = p_expect(p, TOK_INT, "bucket_seconds value");
        if (!p->had_error)
            p->ast.ts_start = bs.ival;
    }

    /* Optional COMPACT_THRESHOLD <n>. */
    p->ast.compact_threshold = 0;
    if (p_peek_kw(p, "COMPACT_THRESHOLD")) {
        hm_lexer_next(&p->lex);
        HmToken ct = p_expect(p, TOK_INT, "compact_threshold value");
        if (!p->had_error)
            p->ast.compact_threshold = ct.ival;
    }

    p->ast.kind = HM_QUERY_CREATE_TABLE;
    p_skip_rest(p);
}

/*
 * DROP HYPEREDGE TABLE <name>
 *
 * Fills:
 *   ast.table = table name (uppercased)
 *   ast.kind  = HM_QUERY_DROP_TABLE
 */
static void parse_drop_hyperedge_table(Parser *p)
{
    /* DROP already consumed. */
    p_expect_kw(p, "HYPEREDGE"); if (p->had_error) return;
    p_expect_kw(p, "TABLE");     if (p->had_error) return;

    HmToken name_tok = p_expect(p, TOK_IDENT, "table name after DROP HYPEREDGE TABLE");
    if (p->had_error) return;
    strncpy(p->ast.table, name_tok.text, sizeof(p->ast.table) - 1);

    p->ast.kind = HM_QUERY_DROP_TABLE;
    p_skip_rest(p);
}

/* ── Phase 4: PSI index DDL parsers ─────────────────────────────────────── */

/*
 * CREATE INDEX ON <table> ( <col> )
 *
 * Fills:
 *   ast.table     = table name (uppercased)
 *   ast.index_col = column name (uppercased)
 *   ast.kind      = HM_QUERY_CREATE_INDEX
 */
static void parse_create_index(Parser *p)
{
    /* CREATE already consumed. */
    p_expect_kw(p, "INDEX"); if (p->had_error) return;
    p_expect_kw(p, "ON");    if (p->had_error) return;

    HmToken tbl = p_expect(p, TOK_IDENT, "table name after CREATE INDEX ON");
    if (p->had_error) return;
    strncpy(p->ast.table, tbl.text, sizeof(p->ast.table) - 1);

    p_expect(p, TOK_LPAREN, "'(' before column name"); if (p->had_error) return;

    HmToken col = p_expect(p, TOK_IDENT, "column name");
    if (p->had_error) return;
    strncpy(p->ast.index_col, col.text, sizeof(p->ast.index_col) - 1);

    p_expect(p, TOK_RPAREN, "')' after column name"); if (p->had_error) return;

    p->ast.kind = HM_QUERY_CREATE_INDEX;
    p_skip_rest(p);
}

/*
 * DROP INDEX ON <table> ( <col> )
 */
static void parse_drop_index(Parser *p)
{
    /* DROP already consumed. */
    p_expect_kw(p, "INDEX"); if (p->had_error) return;
    p_expect_kw(p, "ON");    if (p->had_error) return;

    HmToken tbl = p_expect(p, TOK_IDENT, "table name after DROP INDEX ON");
    if (p->had_error) return;
    strncpy(p->ast.table, tbl.text, sizeof(p->ast.table) - 1);

    p_expect(p, TOK_LPAREN, "'(' before column name"); if (p->had_error) return;

    HmToken col = p_expect(p, TOK_IDENT, "column name");
    if (p->had_error) return;
    strncpy(p->ast.index_col, col.text, sizeof(p->ast.index_col) - 1);

    p_expect(p, TOK_RPAREN, "')' after column name"); if (p->had_error) return;

    p->ast.kind = HM_QUERY_DROP_INDEX;
    p_skip_rest(p);
}

/*
 * CALL show_indexes()
 * SHOW INDEXES [ON <table>]
 *
 * ast.table is optionally set for the ON <table> filter.
 * ast.kind  = HM_QUERY_SHOW_INDEXES
 */
static void parse_show_indexes(Parser *p)
{
    /* "show_indexes" or "INDEXES" keyword already peeked; consume it. */
    hm_lexer_next(&p->lex);

    /* Optional: ON <table> */
    if (p_try_kw(p, "ON")) {
        HmToken tbl = hm_lexer_peek(&p->lex);
        if (tbl.kind == TOK_IDENT) {
            hm_lexer_next(&p->lex);
            strncpy(p->ast.table, tbl.text, sizeof(p->ast.table) - 1);
        }
    }

    p->ast.kind = HM_QUERY_SHOW_INDEXES;
    p_skip_rest(p);
}

/* ── Phase 3: DML helpers ────────────────────────────────────────────────── */

/*
 * Append a field separator (HM_FIELD_SEP = 0x1F) to buf[*pos].
 * Leaves room for the NUL terminator.
 */
static void buf_sep(char *buf, size_t bufsz, size_t *pos)
{
    if (*pos < bufsz - 2)
        buf[(*pos)++] = HM_FIELD_SEP;
}

/*
 * Append str to buf[*pos], honoring the buffer limit.
 */
static void buf_append(char *buf, size_t bufsz, size_t *pos, const char *str)
{
    size_t len = strlen(str);
    if (*pos + len < bufsz - 1) {
        memcpy(buf + *pos, str, len);
        *pos += len;
    }
    buf[*pos] = '\0';
}

/*
 * Parse a single DML value token and append its string representation into
 * out[outsz], advancing the lexer.
 *
 * Recognised forms:
 *   INT [ '.' INT ]   → decimal string  e.g. "42", "3.14"
 *   String literal    → content string  e.g. "alpha" (quotes stripped, case preserved)
 *   Identifier        → uppercased text e.g. "WEDGE", "NULL"
 *   '[' int-list ']'  → compact list    e.g. "[1,2,3]"
 *
 * Returns 1 on success, 0 if no recognisable value token is at the head.
 */
static int parse_value_token(Parser *p, char *out, size_t outsz)
{
    HmToken t = hm_lexer_peek(&p->lex);
    char    buf[HM_AST_VALS_LEN] = {0};

    if (t.kind == TOK_INT) {
        /* May be part of an INT.INT float literal (e.g. 0.01, 3.14). */
        hm_lexer_next(&p->lex);
        if (hm_lexer_peek(&p->lex).kind == TOK_DOT) {
            HmLexer saved = p->lex;
            hm_lexer_next(&p->lex);
            HmToken frac = hm_lexer_peek(&p->lex);
            if (frac.kind == TOK_INT) {
                hm_lexer_next(&p->lex);
                /* Use the raw digit text of the fractional part so leading
                 * zeros are preserved: "0.01" → "0.01", not "0.1". */
                snprintf(buf, sizeof(buf), "%u.%s", t.ival,
                         frac.text[0] ? frac.text : "0");
            } else {
                p->lex = saved;
                snprintf(buf, sizeof(buf), "%u", t.ival);
            }
        } else {
            snprintf(buf, sizeof(buf), "%u", t.ival);
        }
        strncpy(out, buf, outsz - 1);
        out[outsz - 1] = '\0';
        return 1;
    }

    if (t.kind == TOK_STRING || t.kind == TOK_IDENT) {
        hm_lexer_next(&p->lex);
        strncpy(out, t.text, outsz - 1);
        out[outsz - 1] = '\0';
        return 1;
    }

    if (t.kind == TOK_LBRACKET) {
        /* Member list: [ int, int, ... ] */
        hm_lexer_next(&p->lex);                   /* consume '[' */
        size_t pos = 0;
        buf[pos++] = '[';

        int first_elem = 1;
        while (hm_lexer_peek(&p->lex).kind != TOK_RBRACKET &&
               hm_lexer_peek(&p->lex).kind != TOK_EOF) {
            if (!first_elem) {
                if (hm_lexer_peek(&p->lex).kind == TOK_COMMA)
                    hm_lexer_next(&p->lex);
                else break;
            }

            HmToken id = hm_lexer_peek(&p->lex);
            if (id.kind != TOK_INT) break;
            hm_lexer_next(&p->lex);

            /* Add comma separator before every element after the first */
            if (!first_elem && pos < sizeof(buf) - 2)
                buf[pos++] = ',';
            first_elem = 0;

            char tmp[32];
            snprintf(tmp, sizeof(tmp), "%u", id.ival);
            size_t tl = strlen(tmp);
            if (pos + tl + 2 < sizeof(buf) - 1) {
                memcpy(buf + pos, tmp, tl);
                pos += tl;
            }
        }
        if (hm_lexer_peek(&p->lex).kind == TOK_RBRACKET)
            hm_lexer_next(&p->lex);               /* consume ']' */

        buf[pos++] = ']';
        buf[pos]   = '\0';
        strncpy(out, buf, outsz - 1);
        out[outsz - 1] = '\0';
        return 1;
    }

    return 0; /* unrecognised */
}

/*
 * INSERT INTO <table> ( <col>, ... ) VALUES ( <val>, ... )
 *
 * Fills:
 *   ast.table       = table name (uppercased)
 *   ast.insert_cols = comma-separated column list
 *   ast.insert_vals = 0x1F-separated value list (one tuple)
 *   ast.kind        = HM_QUERY_INSERT
 */
static void parse_insert(Parser *p)
{
    /* INSERT already consumed. */
    p_expect_kw(p, "INTO"); if (p->had_error) return;

    HmToken name = p_expect(p, TOK_IDENT, "table name after INSERT INTO");
    if (p->had_error) return;
    strncpy(p->ast.table, name.text, sizeof(p->ast.table) - 1);

    /* Column list: ( col, col, ... ) */
    p_expect(p, TOK_LPAREN, "'(' after table name"); if (p->had_error) return;

    memset(p->ast.insert_cols, 0, sizeof(p->ast.insert_cols));
    size_t col_pos   = 0;
    int    first_col = 1;

    while (!p->had_error) {
        HmToken t = hm_lexer_peek(&p->lex);
        if (t.kind == TOK_RPAREN || t.kind == TOK_EOF) break;

        if (!first_col) {
            if (t.kind != TOK_COMMA) {
                p_error(p, "Expected ',' in INSERT column list, got '%s'", t.text);
                return;
            }
            hm_lexer_next(&p->lex);
            t = hm_lexer_peek(&p->lex);
        }
        if (t.kind != TOK_IDENT) {
            p_error(p, "Expected column name in INSERT, got '%s'", t.text);
            return;
        }
        hm_lexer_next(&p->lex);

        if (!first_col && col_pos < sizeof(p->ast.insert_cols) - 2)
            p->ast.insert_cols[col_pos++] = ',';

        size_t tl = strlen(t.text);
        if (col_pos + tl < sizeof(p->ast.insert_cols) - 1) {
            memcpy(p->ast.insert_cols + col_pos, t.text, tl);
            col_pos += tl;
        }
        first_col = 0;
    }
    p_expect(p, TOK_RPAREN, "')' after INSERT column list"); if (p->had_error) return;

    /* VALUES ( val, val, ... ) */
    p_expect_kw(p, "VALUES"); if (p->had_error) return;
    p_expect(p, TOK_LPAREN, "'(' after VALUES"); if (p->had_error) return;

    memset(p->ast.insert_vals, 0, sizeof(p->ast.insert_vals));
    size_t val_pos   = 0;
    int    first_val = 1;

    while (!p->had_error) {
        HmToken t = hm_lexer_peek(&p->lex);
        if (t.kind == TOK_RPAREN || t.kind == TOK_EOF) break;

        if (!first_val) {
            if (t.kind != TOK_COMMA) {
                p_error(p, "Expected ',' in VALUES list, got '%s'", t.text);
                return;
            }
            hm_lexer_next(&p->lex);
        }

        /* Insert the field separator before each value except the first */
        if (!first_val && val_pos < sizeof(p->ast.insert_vals) - 2)
            p->ast.insert_vals[val_pos++] = HM_FIELD_SEP;

        char vbuf[HM_AST_VALS_LEN] = {0};
        if (!parse_value_token(p, vbuf, sizeof(vbuf))) {
            p_error(p, "Unrecognised value in VALUES list");
            return;
        }
        size_t vl = strlen(vbuf);
        if (val_pos + vl < sizeof(p->ast.insert_vals) - 1) {
            memcpy(p->ast.insert_vals + val_pos, vbuf, vl);
            val_pos += vl;
        }
        first_val = 0;
    }

    p_expect(p, TOK_RPAREN, "')' after VALUES list"); if (p->had_error) return;

    p->ast.kind = HM_QUERY_INSERT;
    p_skip_rest(p);
}

/*
 * DELETE FROM <table> WHERE <where-expr>
 *
 * Fills the same fields as a MATCH ... WHERE (ts_start/ts_end, node_id,
 * predicates) plus:
 *   ast.table = table name
 *   ast.kind  = HM_QUERY_DELETE
 *
 * The Python layer executes the equivalent MATCH, then calls delete() on
 * each result row.
 */
static void parse_delete(Parser *p)
{
    /* DELETE already consumed. */
    p_expect_kw(p, "FROM"); if (p->had_error) return;

    HmToken name = p_expect(p, TOK_IDENT, "table name after DELETE FROM");
    if (p->had_error) return;
    strncpy(p->ast.table, name.text, sizeof(p->ast.table) - 1);

    if (!p_try_kw(p, "WHERE")) {
        p_error(p, "DELETE requires a WHERE clause");
        return;
    }

    /* Reuse the MATCH WHERE parser — sets kind to TPI_RANGE / FMI / FULL_SCAN */
    parse_where(p);
    if (p->had_error) return;

    /* Override kind to DELETE regardless of what parse_where chose */
    p->ast.kind = HM_QUERY_DELETE;
    p_skip_rest(p);
}

/*
 * UPDATE <table> SET col = val [, col = val ...] WHERE <where-expr>
 *
 * Fills:
 *   ast.table      = table name
 *   ast.update_set = field-separated "COL\x1Fval\x1FCOL\x1Fval" string
 *   (WHERE fields identical to MATCH ... WHERE)
 *   ast.kind       = HM_QUERY_UPDATE
 */
static void parse_update(Parser *p)
{
    /* UPDATE already consumed. */
    HmToken name = p_expect(p, TOK_IDENT, "table name after UPDATE");
    if (p->had_error) return;
    strncpy(p->ast.table, name.text, sizeof(p->ast.table) - 1);

    p_expect_kw(p, "SET"); if (p->had_error) return;

    /* SET col = val [, col = val, ...] */
    memset(p->ast.update_set, 0, sizeof(p->ast.update_set));
    size_t set_pos   = 0;
    int    first_set = 1;

    while (!p->had_error && !p_peek_kw(p, "WHERE") &&
           hm_lexer_peek(&p->lex).kind != TOK_EOF) {

        if (!first_set) {
            if (hm_lexer_peek(&p->lex).kind != TOK_COMMA) break;
            hm_lexer_next(&p->lex);               /* consume ',' */
        }

        HmToken col_tok = hm_lexer_peek(&p->lex);
        if (col_tok.kind != TOK_IDENT) {
            p_error(p, "Expected column name in SET clause, got '%s'", col_tok.text);
            return;
        }
        hm_lexer_next(&p->lex);

        p_expect(p, TOK_EQ, "'=' after column name in SET"); if (p->had_error) return;

        char vbuf[HM_AST_VALS_LEN] = {0};
        if (!parse_value_token(p, vbuf, sizeof(vbuf))) {
            p_error(p, "Unrecognised value in SET clause");
            return;
        }

        /* Append: [sep]COL[sep]val */
        if (!first_set && set_pos < sizeof(p->ast.update_set) - 2)
            p->ast.update_set[set_pos++] = HM_FIELD_SEP;

        buf_append(p->ast.update_set, sizeof(p->ast.update_set), &set_pos, col_tok.text);
        buf_sep   (p->ast.update_set, sizeof(p->ast.update_set), &set_pos);
        buf_append(p->ast.update_set, sizeof(p->ast.update_set), &set_pos, vbuf);
        first_set = 0;
    }

    if (!p_try_kw(p, "WHERE")) {
        p_error(p, "UPDATE requires a WHERE clause");
        return;
    }

    /* Reuse the MATCH WHERE parser for the WHERE predicate */
    parse_where(p);
    if (p->had_error) return;

    p->ast.kind = HM_QUERY_UPDATE;
    p_skip_rest(p);
}

/* ── Top-level entry point ───────────────────────────────────────────────── */

HmQueryAst hm_parse_query(const char *cypher)
{
    Parser p;
    memset(&p, 0, sizeof(p));
    hm_lexer_init(&p.lex, cypher ? cypher : "");

    /* Default: parse error with a useful initial message. */
    p.ast.kind = HM_QUERY_PARSE_ERROR;
    strncpy(p.ast.error,
            "Empty query or unrecognised statement. "
            "Supported: MATCH HYPEREDGE, CALL SHOW_HYPEREDGE_TABLES(), "
            "CREATE/DROP HYPEREDGE TABLE, INSERT INTO, DELETE FROM, UPDATE",
            sizeof(p.ast.error) - 1);

    if (hm_lexer_peek(&p.lex).kind == TOK_EOF)
        return p.ast;

    HmToken first = hm_lexer_next(&p.lex); /* consume first token */

    if (hm_tok_is_kw(&first, "MATCH")) {
        parse_match(&p);

    } else if (hm_tok_is_kw(&first, "CALL")) {
        HmToken proc = hm_lexer_peek(&p.lex);
        if (hm_tok_is_kw(&proc, "SHOW_HYPEREDGE_TABLES") ||
            hm_tok_is_kw(&proc, "SHOW_HYPEREDGE_TABLE")) {
            hm_lexer_next(&p.lex);
            parse_show_tables(&p);
        } else if (hm_tok_is_kw(&proc, "GET_HYPEREDGES_BY_TIME_RANGE")) {
            hm_lexer_next(&p.lex);
            parse_get_by_range(&p);
        } else if (hm_tok_is_kw(&proc, "SHOW_INDEXES") ||
                   hm_tok_is_kw(&proc, "SHOW_INDICES")) {
            parse_show_indexes(&p);
        } else {
            p_error(&p, "Unknown CALL procedure: '%s'", proc.text);
        }

    } else if (hm_tok_is_kw(&first, "CREATE")) {
        /* CREATE INDEX ON ... vs CREATE HYPEREDGE TABLE ... */
        if (p_peek_kw(&p, "INDEX"))
            parse_create_index(&p);
        else
            parse_create_hyperedge_table(&p);

    } else if (hm_tok_is_kw(&first, "DROP")) {
        /* DROP INDEX ON ... vs DROP HYPEREDGE TABLE ... */
        if (p_peek_kw(&p, "INDEX"))
            parse_drop_index(&p);
        else
            parse_drop_hyperedge_table(&p);

    } else if (hm_tok_is_kw(&first, "INSERT")) {
        parse_insert(&p);

    } else if (hm_tok_is_kw(&first, "DELETE")) {
        parse_delete(&p);

    } else if (hm_tok_is_kw(&first, "UPDATE")) {
        parse_update(&p);

    } else if (hm_tok_is_kw(&first, "SHOW")) {
        /* SHOW INDEXES [ON <table>]  (alternative to CALL SHOW_INDEXES()) */
        if (p_peek_kw(&p, "INDEXES") || p_peek_kw(&p, "INDICES")) {
            parse_show_indexes(&p);
        } else {
            p_error(&p, "Expected INDEXES or INDICES after SHOW");
        }

    } else {
        p_error(&p, "Expected MATCH, CALL, CREATE, DROP, INSERT, DELETE, UPDATE, or SHOW — got '%s'",
                first.text);
    }

    return p.ast;
}
