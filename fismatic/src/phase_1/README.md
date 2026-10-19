# FedRAMP Parser Web Interface

## Setup and Installation

1. Create and activate the conda environment:
   ```bash
   conda env create -f environment.yml
   conda activate fismatic
   ```

2. Run the web interface:
   ```bash
   cd fismatic/src/phase_1
   streamlit run app.py
   ```

## Using the Web Interface

1. The interface will open in your default web browser
2. Choose a sample text or enter custom text to parse
3. Click "Parse Text" to analyze the input
4. View the results and download the memory profile report

## Memory Profiling

The web interface uses Memray to profile memory usage during parsing. Each parsing operation generates a detailed HTML report showing:
- Memory allocation patterns
- Memory usage over time
- Allocation hot spots
- Call stack information

Download the memory profile report after parsing to analyze the parser's memory behavior.