"""Recognize one fixed PostgreSQL query AST; never execute supplied SQL."""

import logging
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import ORIGINAL

MAX_BYTES=64*1024


class SQLIntakeError(ValueError):
    def __init__(self,status,message):
        super().__init__(message)
        self.status=status


def _parse(sql):
    # SQLGlot may log raw text for unsupported command fallbacks. Do not echo input.
    logger=logging.getLogger('sqlglot')
    suppress=lambda record: False
    logger.addFilter(suppress)
    try:
        return sqlglot.parse(sql,read='postgres',error_level=sqlglot.ErrorLevel.RAISE,
                             error_message_context=0,max_errors=1,max_nodes=10000)
    except (ParseError,TokenError,RecursionError,ValueError):
        raise SQLIntakeError('invalid_sql','Unable to parse PostgreSQL SQL; check syntax and quoting') from None
    finally:
        logger.removeFilter(suppress)


def recognize_date_query(sql):
    statements=_parse(sql)
    if not statements or all(statement is None for statement in statements):
        raise SQLIntakeError('invalid_sql','SQL file must contain a statement')
    if len(statements)!=1 or not isinstance(statements[0],exp.Select):
        raise SQLIntakeError('unsupported_query_shape','Expected one SELECT matching the orders date-filter baseline')
    baseline=_parse(ORIGINAL)[0]
    query=statements[0].copy()
    # Locate only the baseline's right-hand DATE literal; do not search arbitrary literals.
    condition=query.args.get('where')
    equality=condition.this if isinstance(condition,exp.Where) else None
    rhs=equality.expression if isinstance(equality,exp.EQ) else None
    literal=rhs.this if isinstance(rhs,exp.Cast) else None
    if not isinstance(literal,exp.Literal) or not literal.is_string:
        raise SQLIntakeError('unsupported_query_shape','Expected the baseline date literal and complete query structure')
    value=literal.this
    # Compare the entire AST, replacing only the date string. No rewrites or partial matching.
    literal.set('this',baseline.args['where'].this.expression.this.this)
    if query!=baseline:
        raise SQLIntakeError('unsupported_query_shape','Only the baseline query date literal may change')
    try:
        context=OrderDateContext.parse(value)
    except ValueError as error:
        raise SQLIntakeError('invalid_date',str(error)) from None
    return context.target.isoformat()


def read_date_query(path):
    try:
        with Path(path).open('rb') as source:data=source.read(MAX_BYTES+1)
    except OSError:
        raise SQLIntakeError('sql_file_error','Unable to read the specified SQL file') from None
    if len(data)>MAX_BYTES:
        raise SQLIntakeError('sql_input_too_large','SQL input exceeds 64 KiB limit')
    try:
        text=data.decode('utf-8')
    except UnicodeError:
        raise SQLIntakeError('invalid_sql','SQL file must be UTF-8 text') from None
    return recognize_date_query(text)
