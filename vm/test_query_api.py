#!/usr/bin/env python3
"""Pure-stdlib unit tests for vm/query_api.py — no DB, no psycopg2 required.

Run: python3 vm/test_query_api.py
"""
import hmac
import os
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from query_api import validate_sql, json_safe  # noqa: E402


class TestValidateSqlAllowed(unittest.TestCase):
    def assertAllowed(self, sql):
        err = validate_sql(sql)
        self.assertIsNone(err, f'expected allowed, got error: {err!r} for sql={sql!r}')

    def test_plain_select(self):
        self.assertAllowed('SELECT 1')

    def test_plain_select_from_table(self):
        self.assertAllowed('SELECT id, name FROM users WHERE id = 1')

    def test_with_cte(self):
        self.assertAllowed('WITH t AS (SELECT 1 AS x) SELECT * FROM t')

    def test_subqueries(self):
        self.assertAllowed(
            'SELECT * FROM (SELECT id FROM users WHERE id IN '
            '(SELECT user_id FROM orders)) sub'
        )

    def test_string_containing_delete_keyword(self):
        self.assertAllowed("SELECT * FROM notes WHERE body = 'please delete this'")

    def test_string_containing_semicolon(self):
        self.assertAllowed("SELECT * FROM notes WHERE body = 'a; b; c'")

    def test_quoted_identifiers(self):
        self.assertAllowed('SELECT "user"."name" FROM "user"')

    def test_dollar_quoted_string_containing_drop(self):
        self.assertAllowed("SELECT $tag$this text says DROP TABLE foo$tag$ AS msg")

    def test_dollar_quoted_no_tag(self):
        self.assertAllowed("SELECT $$literal DROP TABLE text$$ AS msg")

    def test_trailing_semicolon(self):
        self.assertAllowed('SELECT 1;')

    def test_trailing_semicolon_with_whitespace(self):
        self.assertAllowed('SELECT 1;   \n')

    def test_leading_newlines_and_comments(self):
        self.assertAllowed('\n-- a comment\nSELECT 1')

    def test_leading_block_comment(self):
        self.assertAllowed('/* note */ SELECT 1')

    def test_comment_obfuscated_keyword_still_parses(self):
        # Splitting SELECT with a block comment must NOT be treated as an
        # attempt to hide a forbidden keyword -- it skeletonizes back to a
        # plain SELECT and must be allowed.
        self.assertAllowed('SEL/**/ECT 1')

    def test_nested_block_comments(self):
        self.assertAllowed('SELECT 1 /* outer /* inner */ still outer */')

    def test_lowercase_select(self):
        self.assertAllowed('select 1')

    def test_mixed_case_with(self):
        self.assertAllowed('With t As (Select 1) Select * From t')

    def test_into_inside_string_allowed(self):
        self.assertAllowed("SELECT * FROM t WHERE note = 'copy into folder'")


class TestValidateSqlRejected(unittest.TestCase):
    def assertRejected(self, sql):
        err = validate_sql(sql)
        self.assertIsNotNone(err, f'expected rejection for sql={sql!r}')
        return err

    def test_empty_string(self):
        self.assertRejected('')

    def test_whitespace_only(self):
        self.assertRejected('   \n\t  ')

    def test_none(self):
        self.assertRejected(None)

    def test_insert(self):
        self.assertRejected("INSERT INTO users (name) VALUES ('x')")

    def test_update(self):
        self.assertRejected("UPDATE users SET name = 'x'")

    def test_delete(self):
        self.assertRejected('DELETE FROM users')

    def test_drop(self):
        self.assertRejected('DROP TABLE users')

    def test_alter(self):
        self.assertRejected('ALTER TABLE users ADD COLUMN x int')

    def test_create(self):
        self.assertRejected('CREATE TABLE x (id int)')

    def test_truncate(self):
        self.assertRejected('TRUNCATE users')

    def test_grant(self):
        self.assertRejected('GRANT ALL ON users TO someone')

    def test_revoke(self):
        self.assertRejected('REVOKE ALL ON users FROM someone')

    def test_copy(self):
        self.assertRejected("COPY users TO '/tmp/out.csv'")

    def test_call(self):
        self.assertRejected('CALL some_procedure()')

    def test_do_block(self):
        self.assertRejected("DO $$ BEGIN RAISE NOTICE 'hi'; END $$")

    def test_set(self):
        self.assertRejected('SET search_path TO public')

    def test_execute(self):
        self.assertRejected('EXECUTE some_prepared_stmt')

    def test_vacuum(self):
        self.assertRejected('VACUUM users')

    def test_analyze(self):
        self.assertRejected('ANALYZE users')

    def test_lock(self):
        self.assertRejected('LOCK TABLE users')

    def test_comment_statement(self):
        self.assertRejected("COMMENT ON TABLE users IS 'x'")

    def test_select_into(self):
        self.assertRejected('SELECT * INTO new_table FROM users')

    def test_multi_statement(self):
        self.assertRejected('SELECT 1; DROP TABLE x')

    def test_multi_statement_two_selects(self):
        self.assertRejected('SELECT 1; SELECT 2')

    def test_comment_obfuscated_drop_still_caught(self):
        self.assertRejected('DROP/*x*/ TABLE users')

    def test_pg_sleep(self):
        self.assertRejected('SELECT pg_sleep(10)')

    def test_dblink(self):
        self.assertRejected("SELECT * FROM dblink('conn', 'select 1') AS t(x int)")

    def test_pg_read_file(self):
        self.assertRejected("SELECT pg_read_file('/etc/passwd')")

    def test_pg_ls_dir(self):
        self.assertRejected("SELECT pg_ls_dir('/tmp')")

    def test_lo_import(self):
        self.assertRejected("SELECT lo_import('/etc/passwd')")

    def test_lo_export(self):
        self.assertRejected("SELECT lo_export(12345, '/tmp/out')")

    def test_pg_terminate_backend(self):
        self.assertRejected('SELECT pg_terminate_backend(123)')

    def test_pg_cancel_backend(self):
        self.assertRejected('SELECT pg_cancel_backend(123)')

    def test_does_not_start_with_select_or_with(self):
        self.assertRejected('EXPLAIN SELECT 1')

    def test_forbidden_keyword_case_insensitive(self):
        self.assertRejected('select 1; drop table x')

    def test_forbidden_keyword_not_fooled_by_prefix_column_name(self):
        # Sanity check the reverse direction: a column literally named
        # "created_at" must NOT trip the CREATE keyword check.
        self.assertAllowed_helper('SELECT created_at FROM users')

    def assertAllowed_helper(self, sql):
        err = validate_sql(sql)
        self.assertIsNone(err, f'expected allowed, got error: {err!r} for sql={sql!r}')


class TestAuthCompare(unittest.TestCase):
    """The handler's _authed() uses hmac.compare_digest directly; exercise
    the same primitive/semantics used there."""

    def test_matching_secret(self):
        self.assertTrue(hmac.compare_digest('s3cret', 's3cret'))

    def test_non_matching_secret(self):
        self.assertFalse(hmac.compare_digest('s3cret', 'wrong'))

    def test_empty_vs_nonempty(self):
        self.assertFalse(hmac.compare_digest('', 's3cret'))


class TestJsonSafe(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(json_safe(None))

    def test_bool_passthrough(self):
        self.assertIs(json_safe(True), True)
        self.assertIs(json_safe(False), False)

    def test_int_passthrough(self):
        self.assertEqual(json_safe(42), 42)

    def test_float_passthrough(self):
        self.assertEqual(json_safe(3.14), 3.14)

    def test_str_passthrough(self):
        self.assertEqual(json_safe('hello'), 'hello')

    def test_decimal_to_float(self):
        result = json_safe(Decimal('12.50'))
        self.assertIsInstance(result, float)
        self.assertEqual(result, 12.5)

    def test_date_to_iso(self):
        self.assertEqual(json_safe(date(2026, 8, 4)), '2026-08-04')

    def test_datetime_to_iso(self):
        result = json_safe(datetime(2026, 8, 4, 10, 30, 0))
        self.assertEqual(result, '2026-08-04T10:30:00')

    def test_bytes_decoded(self):
        self.assertEqual(json_safe(b'hello'), 'hello')

    def test_bytes_non_utf8_hex_fallback(self):
        result = json_safe(b'\xff\xfe')
        self.assertIsInstance(result, str)

    def test_other_type_stringified(self):
        class Weird:
            def __str__(self):
                return 'weird-value'
        self.assertEqual(json_safe(Weird()), 'weird-value')


if __name__ == '__main__':
    unittest.main(verbosity=2)
