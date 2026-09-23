/*
 * lexer.h — HyperMesh Cypher Tokenizer
 *
 * Hand-written single-pass tokenizer for the HyperMesh Cypher query dialect.
 * All identifiers are upper-cased on emission so the parser can do simple
 * keyword comparisons without caring about the original casing.
 *
 * Usage:
 *   HmLexer lex;
 *   hm_lexer_init(&lex, "MATCH HYPEREDGE (he:CoProximity) WHERE he.event_ts >= 100");
 *   while (hm_lexer_peek(&lex).kind != TOK_EOF) {
 *       HmToken t = hm_lexer_next(&lex);
 *       // process t
 *   }
 */

#ifndef HM_LEXER_H
#define HM_LEXER_H

#include <stdint.h>

/* ── Token kinds ─────────────────────────────────────────────────────────── */

typedef enum {
    TOK_EOF       = 0,
    TOK_IDENT,        /* [a-zA-Z_][a-zA-Z0-9_]* — upper-cased in .text     */
    TOK_INT,          /* [0-9]+   — value in .ival                           */
    TOK_STRING,       /* 'text' or "text" — content (no quotes) in .text     */
    TOK_STAR,         /* *                                                    */
    TOK_LPAREN,       /* (                                                    */
    TOK_RPAREN,       /* )                                                    */
    TOK_LBRACKET,     /* [                                                    */
    TOK_RBRACKET,     /* ]                                                    */
    TOK_LBRACE,       /* {                                                    */
    TOK_RBRACE,       /* }                                                    */
    TOK_COLON,        /* :                                                    */
    TOK_COMMA,        /* ,                                                    */
    TOK_DOT,          /* .                                                    */
    TOK_GEQ,          /* >=                                                   */
    TOK_LEQ,          /* <=                                                   */
    TOK_GT,           /* >                                                    */
    TOK_LT,           /* <                                                    */
    TOK_EQ,           /* =                                                    */
    TOK_SLASH,        /* /                                                    */
    TOK_ERROR,        /* unrecognised character — .text holds the char        */
} HmTokenKind;

#define HM_TOK_MAX_TEXT 256

/* ── Token ───────────────────────────────────────────────────────────────── */

typedef struct {
    HmTokenKind kind;
    uint32_t    ival;                  /* valid when kind == TOK_INT           */
    char        text[HM_TOK_MAX_TEXT]; /* upper-cased ident / string content  */
    int         text_len;
} HmToken;

/* ── Lexer state ─────────────────────────────────────────────────────────── */

typedef struct {
    const char *pos;    /* next character to examine                         */
    const char *end;    /* one past the last valid byte                      */
    HmToken     cur;    /* one-token look-ahead (already scanned)            */
} HmLexer;

/* ── Public API ──────────────────────────────────────────────────────────── */

/*
 * Initialise the lexer and scan the first token into cur.
 * input must remain valid for the lifetime of the lexer.
 */
void    hm_lexer_init(HmLexer *lex, const char *input);

/*
 * Return the current look-ahead token without advancing.
 */
HmToken hm_lexer_peek(const HmLexer *lex);

/*
 * Consume and return the current look-ahead token; advance to the next.
 */
HmToken hm_lexer_next(HmLexer *lex);

/*
 * Return 1 if t is an identifier whose text matches keyword (already upper).
 */
int hm_tok_is_kw(const HmToken *t, const char *keyword);

#endif /* HM_LEXER_H */
