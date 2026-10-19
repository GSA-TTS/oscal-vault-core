import pandas as pd
from fismatic.core import control_set_for


def create_detailed_control_table(input_file):
    # Parse the document
    control_set = control_set_for(input_file)

    controls_data = []
    for control in control_set.controls:
        # Base control information
        control_data = {
            "Control ID": control.normalized_name(),
            "Responsible Role": control.responsible_role,
            "Implementation Status": control.imp_status,
            "Origination": control.origination,
        }

        # Add implementation details
        # Assuming implementations might have parts like 'a', 'b', 'c', etc.
        for part, impl in control.implementation.items():
            control_data[f"Part {part.upper()}"] = str(impl)

        controls_data.append(control_data)

    # Create DataFrame
    df = pd.DataFrame(controls_data)

    # Sort by Control ID
    df = df.sort_values("Control ID")

    return df


# Usage example:
def export_control_table(input_file, output_format="excel"):
    table = create_detailed_control_table(input_file)

    if output_format.lower() == "excel":
        # Export to Excel with formatting
        with pd.ExcelWriter("control_table.xlsx", engine="xlsxwriter") as writer:
            table.to_excel(writer, sheet_name="Controls", index=False)

            # Get workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets["Controls"]

            # Add formatting
            header_format = workbook.add_format(
                {"bold": True, "bg_color": "#D3D3D3", "border": 1}
            )

            # Format headers
            for col_num, value in enumerate(table.columns.values):
                worksheet.write(0, col_num, value, header_format)

            # Autofit column widths
            for i, col in enumerate(table.columns):
                max_length = max(table[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.set_column(i, i, max_length)

    elif output_format.lower() == "csv":
        table.to_csv("control_table.csv", index=False)

    elif output_format.lower() == "html":
        html = table.to_html(classes="table table-striped", border=1, index=False)
        with open("control_table.html", "w") as f:
            f.write(html)


# Example usage:
if __name__ == "__main__":
    # Export to different formats
    input_file = "your_document.docx"
    export_control_table(input_file, "excel")  # Creates control_table.xlsx
    export_control_table(input_file, "csv")  # Creates control_table.csv
    export_control_table(input_file, "html")  # Creates control_table.html
