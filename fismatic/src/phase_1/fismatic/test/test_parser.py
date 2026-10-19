import pytest
from unittest.mock import MagicMock
from .. import parser
from ..docx_parser import DocxParser


@pytest.mark.parametrize(
    "test_input,expected",
    [
        ("control summary information", True),
        ("Control Summary Information", True),
        ("Control Enhancement Summary Information", True),
        ("something else", False),
    ],
)
def test_is_control_heading(test_input, expected):
    assert parser.is_control_heading(test_input) == expected


def _make_mock_table(header_texts, data_texts):
    """Build a minimal mock docx table with two rows."""
    table = MagicMock()

    def make_row(cell_texts):
        row = MagicMock()
        cells = []
        for t in cell_texts:
            cell = MagicMock()
            cell.text = t
            cells.append(cell)
        row.cells = cells
        return row

    table.rows = [make_row(header_texts), make_row(data_texts)]
    return table


@pytest.mark.parametrize(
    "header_texts,data_texts,expected",
    [
        # Case 1: Figma SaaS-CRM "Responsibility Summary Information" layout
        # Header has 'control' + 'responsibility summary information';
        # data row contains 'leveraged saas' (status keyword) — should be >=2 matches.
        (
            ["Control", "Responsibility Summary Information", "SaaS Provider"],
            ["AC-1", "leveraged saas", "The customer must configure logging."],
            True,
        ),
        # Case 2: Unrelated table with no control signals — should be False
        (
            ["Name", "Date", "Notes"],
            ["Alice", "2024-01-01", "some note"],
            False,
        ),
    ],
)
def test_is_control_table_saas_crm(header_texts, data_texts, expected):
    """is_control_table recognises Figma SaaS-CRM responsibility-layout tables
    and correctly rejects unrelated tables."""
    # DocxParser.__init__ requires a real docx path; instantiate a bare object
    # to test only the is_control_table method in isolation.
    parser_obj = DocxParser.__new__(DocxParser)
    table = _make_mock_table(header_texts, data_texts)
    assert parser_obj.is_control_table(table) == expected
