"""Recognize one fixed PostgreSQL query AST; never execute supplied SQL."""

import logging
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import ORIGINAL
from sql_optimization_agent.tools.order_items_benchmark import JOIN_COUNT, SCENARIO as ITEMS_SCENARIO

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


def _read_sql(path):
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
    return text


def read_date_query(path):
    return recognize_date_query(_read_sql(path))


def recognize_query(sql):
    try:
        return "orders_date_filter_2025_06_15", recognize_date_query(sql)
    except SQLIntakeError as error:
        if error.status != 'unsupported_query_shape':
            raise
    statements = _parse(sql)
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SQLIntakeError('unsupported_query_shape', 'Expected one supported SELECT')
    condition=statements[0].args.get('where')
    predicate=condition.this if isinstance(condition,exp.Where) else None
    if isinstance(predicate,exp.Between):
        from sql_optimization_agent.date_interval import ORIGINAL as INTERVAL_SQL, bounds
        baseline=_parse(INTERVAL_SQL.replace('%s', "'2000-01-01'",1).replace('%s', "'2000-01-02'",1))[0]
        query=statements[0].copy(); between=query.args['where'].this
        values=[]
        for key in ('low','high'):
            rhs=between.args[key]
            if not (isinstance(rhs,exp.Cast) and isinstance(rhs.this,exp.Literal) and rhs.this.is_string):
                raise SQLIntakeError('unsupported_query_shape','Expected DATE literal interval boundaries')
            values.append(rhs.this.this)
            rhs.this.set('this',baseline.args['where'].this.args[key].this.this)
        if query!=baseline:
            raise SQLIntakeError('unsupported_query_shape','Only interval dates may change')
        try:bounds(*values)
        except ValueError as error:raise SQLIntakeError('invalid_date',str(error)) from None
        return 'orders_date_interval', values
    # Render only application-owned placeholders into AST sentinels, never execute them.
    template = JOIN_COUNT.replace('%s', "'2000-01-01 00:00:00'", 1).replace('%s', "'2000-01-02 00:00:00'", 1)
    baseline = _parse(template)[0]
    query = statements[0].copy()
    where = query.args.get('where')
    condition = where.this if isinstance(where, exp.Where) else None
    if not (isinstance(condition, exp.And) and isinstance(condition.this, exp.GTE)
            and isinstance(condition.expression, exp.LT)):
        raise SQLIntakeError('unsupported_query_shape', 'Expected the exact count JOIN baseline and half-open day bounds')
    literals = []
    for bound in (condition.this, condition.expression):
        rhs = bound.expression
        if not (isinstance(rhs, exp.Cast) and isinstance(rhs.this, exp.Literal) and rhs.this.is_string):
            raise SQLIntakeError('unsupported_query_shape', 'Expected timestamp literal boundaries')
        literals.append(rhs.this)
    values = [literal.this for literal in literals]
    expected = baseline.args['where'].this
    for literal, bound in zip(literals, (expected.this, expected.expression)):
        literal.set('this', bound.expression.this.this)
    if query != baseline:
        raise SQLIntakeError('unsupported_query_shape', 'Only the count JOIN baseline day boundaries may change')
    try:
        context = OrderDateContext.parse(values[0][:10])
        if values != [context.start.isoformat(sep=' '), context.end.isoformat(sep=' ')]:
            raise ValueError
    except ValueError:
        raise SQLIntakeError('invalid_date', 'Expected valid target-day midnight and exactly next-day midnight') from None
    return ITEMS_SCENARIO, context.target.isoformat()


def read_query(path):
    return recognize_query(_read_sql(path))
