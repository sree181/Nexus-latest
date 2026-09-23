/*
 * lexer.c — HyperMesh Cypher Tokenizer implementation
 */

#include "lexer.h"
#include <ctype.h>
#include <string.h>
#include <stdio.h>

/* ── Internal: skip whitespace and // line comments ─────────────────────── */

static void skip_ws(HmLexer *lex)
{
    while (lex->pos < lex->end) {
        if (isspace((unsigned char)*lex->pos)) {
            lex->pos++;
        } else if (lex->pos + 1 < lex->end &&
                   lex->pos[0] == '/' && lex->pos[1] == '/') {
            /* line comment: skip to end of line */
            while (lex->pos < lex->end && *lex->pos != '\n')
                lex->pos++;
        } else {
            break;
        }
    }
}

/* ── Internal: scan one token from lex->pos ─────────────────────────────── */

static HmToken scan_one(HmLexer *lex)
{
    HmToken tok;
    memset(&tok, 0, sizeof(tok));

    skip_ws(lex);

    if (lex->pos >= lex->end) {
        tok.kind = TOK_EOF;
        return tok;
    }

    char c = *lex->pos;

    /* ── Single-character tokens ─────────────────────────────────────────── */
    switch (c) {
        case '(': lex->pos++; tok.kind = TOK_LPAREN;   return tok;
        case ')': lex->pos++; tok.kind = TOK_RPAREN;   return tok;
        case '[': lex->pos++; tok.kind = TOK_LBRACKET; return tok;
        case ']': lex->pos++; tok.kind = TOK_RBRACKET; return tok;
        case '{': lex->pos++; tok.kind = TOK_LBRACE;   return tok;
        case '}': lex->pos++; tok.kind = TOK_RBRACE;   return tok;
        case ':': lex->pos++; tok.kind = TOK_COLON;    return tok;
        case ',': lex->pos++; tok.kind = TOK_COMMA;    return tok;
        case '.': lex->pos++; tok.kind = TOK_DOT;      return tok;
        case '*': lex->pos++; tok.kind = TOK_STAR;     return tok;
        case '=': lex->pos++; tok.kind = TOK_EQ;       return tok;
        case '/': lex->pos++; tok.kind = TOK_SLASH;    return tok;
        default: break;
    }

    /* ── Two-character tokens: >= <= ─────────────────────────────────────── */
    if (c == '>' && lex->pos + 1 < lex->end && lex->pos[1] == '=') {
        lex->pos += 2; tok.kind = TOK_GEQ; return tok;
    }
    if (c == '<' && lex->pos + 1 < lex->end && lex->pos[1] == '=') {
        lex->pos += 2; tok.kind = TOK_LEQ; return tok;
    }
    if (c == '>') { lex->pos++; tok.kind = TOK_GT; return tok; }
    if (c == '<') { lex->pos++; tok.kind = TOK_LT; return tok; }

    /* ── Integer literal ─────────────────────────────────────────────────── */
    if (isdigit((unsigned char)c)) {
        uint32_t val  = 0;
        int      tlen = 0;
        /* Record the raw digit string in text[] so callers can reproduce the
         * exact representation (e.g. "01" from "0.01" stays "01", not "1"). */
        while (lex->pos < lex->end && isdigit((unsigned char)*lex->pos)) {
            val = val * 10u + (uint32_t)(*lex->pos - '0');
            if (tlen < HM_TOK_MAX_TEXT - 1)
                tok.text[tlen++] = *lex->pos;
            lex->pos++;
        }
        tok.text[tlen] = '\0';
        tok.kind = TOK_INT;
        tok.ival = val;
        return tok;
    }

    /* ── String literal: 'text' or "text" ───────────────────────────────── */
    if (c == '\'' || c == '"') {
        char delim = c;
        lex->pos++;
        int i = 0;
        while (lex->pos < lex->end && *lex->pos != delim) {
            if (i < HM_TOK_MAX_TEXT - 1)
                tok.text[i++] = *lex->pos;
            lex->pos++;
        }
        if (lex->pos < lex->end)
            lex->pos++; /* consume closing delimiter */
        tok.text[i]  = '\0';
        tok.text_len = i;
        tok.kind     = TOK_STRING;
        return tok;
    }

    /* ── Identifier / keyword ─────────────────────────────────────────────
     * Upper-case on the fly so the parser can do strcmp against keywords.   */
    if (isalpha((unsigned char)c) || c == '_') {
        int i = 0;
        while (lex->pos < lex->end &&
               (isalnum((unsigned char)*lex->pos) || *lex->pos == '_')) {
            if (i < HM_TOK_MAX_TEXT - 1)
                tok.text[i++] = (char)toupper((unsigned char)*lex->pos);
            lex->pos++;
        }
        tok.text[i]  = '\0';
        tok.text_len = i;
        tok.kind     = TOK_IDENT;
        return tok;
    }

    /* ── Unrecognised ────────────────────────────────────────────────────── */
    lex->pos++;
    tok.kind     = TOK_ERROR;
    tok.text[0]  = c;
    tok.text[1]  = '\0';
    tok.text_len = 1;
    return tok;
}

/* ── Public API ──────────────────────────────────────────────────────────── */

void hm_lexer_init(HmLexer *lex, const char *input)
{
    lex->pos = input;
    lex->end = input + strlen(input);
    lex->cur = scan_one(lex);
}

HmToken hm_lexer_peek(const HmLexer *lex)
{
    return lex->cur;
}

HmToken hm_lexer_next(HmLexer *lex)
{
    HmToken t = lex->cur;
    lex->cur  = scan_one(lex);
    return t;
}

int hm_tok_is_kw(const HmToken *t, const char *keyword)
{
    return t->kind == TOK_IDENT && strcmp(t->text, keyword) == 0;
}
