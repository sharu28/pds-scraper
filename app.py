import gradio as gr
import os
from myscript import run_my_script

def process_file(uploaded_file_path):
    if not uploaded_file_path:
        return "No file uploaded.", None, None

    # uploaded_file_path is a string path to the uploaded file
    output_excel_path, output_zip_path = run_my_script(uploaded_file_path)

    # Return up to three outputs to match your outputs=...
    return (
        "Processing complete! Download your files below:",
        output_excel_path,
        output_zip_path
    )

demo = gr.Interface(
    fn=process_file,
    # critical: type="filepath" returns a string path 
    inputs=gr.File(type="filepath", label="Upload your Excel file here"),
    outputs=[
        gr.Textbox(label="Processing Status"),
        gr.File(label="Processed Excel File"),
        gr.File(label="ZIP File (if available)")
    ],
    title="PDS Scraper & Validator",
    description="Upload an Excel file, run the scraper, download the results."
)

if __name__ == "__main__":
     demo.launch(server_port=int(os.environ.get("PORT", 7860)), server_name="0.0.0.0")
