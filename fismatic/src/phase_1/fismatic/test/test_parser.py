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


def test_extract_implementation_details_skips_spaced_enhancement_header():
    """Header rows like 'IA-05 (01)' must be skipped when control_id is normalized to 'IA-05(01)'."""
    table = _make_mock_table(
        ["IA-05 (01)", "Control Enhancement Summary Information"],
        ["Part a", "This control is fully inherited from cloud.gov."],
    )
    details = parser.extract_implementation_details(
        {"table": table, "is_control_table": True, "control_id": "IA-05(01)"}
    )
    assert details == {
        "implementation": {"a": "This control is fully inherited from cloud.gov."}
    }


def test_extract_implementation_details_ignores_non_part_rows_in_continuation_table():
    """Continuation tables (is_control_table=False) must only extract Part/Req rows, not Acronym glossaries."""
    glossary = _make_mock_table(
        ["Acronym", "Full Name"],
        ["AMI", "Amazon Machine Image"],
    )
    assert parser.extract_implementation_details({"table": glossary}) == {}

    continuation = _make_mock_table(
        ["Part d", "Continuation narrative for part d."],
        ["Part e", "Continuation narrative for part e."],
    )
    assert parser.extract_implementation_details({"table": continuation}) == {
        "implementation": {
            "d": "Continuation narrative for part d.",
            "e": "Continuation narrative for part e.",
        }
    }


def test_extract_implementation_details_preserves_row3_crm_and_responsible_role_prose():
    """Merged Row-3 CRM statements and merged narratives containing 'responsible' and 'role' must not be dropped."""
    crm_text = "cloud.gov Pages: Site owners are responsible for assigning org and space roles."
    tenant_text = (
        "GSA Pages Tenant: Managers are responsible for repository role assignments."
    )
    table = _make_mock_table([crm_text, crm_text], [tenant_text, tenant_text])
    details = parser.extract_implementation_details(
        {"table": table, "is_control_table": True, "control_id": "CM-05"}
    )
    assert crm_text in details["implementation"]["a"]
    assert tenant_text in details["implementation"]["a"]


def _make_multi_row_mock_table(rows_cells):
    table = MagicMock()
    rows = []
    for cell_texts in rows_cells:
        row = MagicMock()
        cells = []
        for t in cell_texts:
            cell = MagicMock()
            cell.text = t
            cells.append(cell)
        row.cells = cells
        rows.append(row)
    table.rows = rows

    def _cell(r, c):
        return rows[r].cells[c]

    table.cell = _cell
    return table


def test_split_cis_and_continuation_tables_preserve_status_and_parts():
    """Split CIS summary + continuation tables (Rev 4 / early Rev 5) must preserve
    Implementation Status/Type and merge continuation Part rows without overwriting."""
    cis_table = _make_multi_row_mock_table(
        [
            ["AC-1", "Control Summary Information"],
            ["Responsible Role: System Owner", "Responsible Role: System Owner"],
            ["Parameter AC-1(a): Annually", "Parameter AC-1(a): Annually"],
            [
                "Implementation Type (check all that apply):\n☒ Implemented\n☐ Planned",
                "Implementation Type (check all that apply):\n☒ Implemented\n☐ Planned",
            ],
            [
                "Control Origination (check all that apply):\n☒ Service Provider System Specific",
                "Control Origination (check all that apply):\n☒ Service Provider System Specific",
            ],
        ]
    )
    cont_table = _make_multi_row_mock_table(
        [
            [
                "AC-1 What is the solution and how is it implemented?",
                "AC-1 What is the solution and how is it implemented?",
            ],
            ["Part a", "Access control policy is documented and reviewed annually."],
            ["Part b", "Procedures are maintained by the ISSO."],
        ]
    )

    parser_obj = DocxParser.__new__(DocxParser)
    parser_obj.config = {}
    parser_obj.tables = [cis_table, cont_table]

    assert parser_obj.is_control_table(cis_table) is True
    assert parser_obj.is_control_table(cont_table) is False

    controls = parser_obj.get_controls()
    assert list(controls.keys()) == ["AC-1"]
    ctrl = controls["AC-1"]
    assert ctrl.implementation_status == {"state": "implemented"}
    assert ctrl.origination == ["system-specific"]
    assert ctrl.implementation == {
        "a": "Access control policy is documented and reviewed annually.",
        "b": "Procedures are maintained by the ISSO.",
    }


def test_extract_implementation_details_separates_headers_and_long_prompts_from_narrative():
    """Structural summary headers and long instructional prompts remain excluded while narrative mentioning 'Control Summary Information' survives."""
    long_prompt = (
        "PT-07 Describe how the control is implemented across all environments, "
        "including automated mechanisms, review frequency, and role responsibilities."
    )
    narrative = (
        "Part PT-07_01_: Control Summary Information: Synthetic tenant safeguards "
        "for specific categories of records are enforced through role-based access."
    )
    table = _make_multi_row_mock_table(
        [
            ["PT-07 Control Summary Information", "PT-07 Control Summary Information"],
            [long_prompt, long_prompt],
            [narrative, narrative],
        ]
    )
    details = parser.extract_implementation_details(
        {"table": table, "is_control_table": True, "control_id": "PT-07"}
    )
    assert details == {"implementation": {"a": narrative}}


def test_extract_implementation_details_preserves_named_parts_and_crm_rows():
    """Named Part rows and CRM responsibility rows survive while Responsibility Summary headers are excluded."""
    part_table = _make_multi_row_mock_table(
        [
            ["AC-02", "Control Summary Information"],
            ["Part a", "Part a references the Control Summary Information table."],
            ["Part b", "Part b defines review cadence."],
        ]
    )
    assert parser.extract_implementation_details(
        {"table": part_table, "is_control_table": True, "control_id": "AC-02"}
    ) == {
        "implementation": {
            "a": "Part a references the Control Summary Information table.",
            "b": "Part b defines review cadence.",
        }
    }

    crm_table = _make_multi_row_mock_table(
        [
            ["CR-1", "Responsibility Summary Information"],
            [
                "Implementation Status:\n☒ Implemented",
                "Implementation Status:\n☒ Implemented",
            ],
            ["Controls Reference: AC-2, IA-2", "Controls Reference: AC-2, IA-2"],
            [
                "Customer Responsibility: Configure tenant role assignments.",
                "Customer Responsibility: Configure tenant role assignments.",
            ],
        ]
    )
    crm_details = parser.extract_implementation_details(
        {"table": crm_table, "is_control_table": True, "control_id": "CR-1"}
    )
    assert (
        "Responsibility Summary Information" not in crm_details["implementation"]["a"]
    )
    assert (
        "Customer Responsibility: Configure tenant role assignments."
        in crm_details["implementation"]["a"]
    )
