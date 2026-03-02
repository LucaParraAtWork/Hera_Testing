HERA Smoke Test Runner

This script automates the smoke testing process for the HERA Nominations and Profiles modules. It simulates user actions (Import, Confirm, Reject) and generates a detailed, formatted Excel report.

⚠️ CRITICAL PRE-REQUISITES (Before Running)

1. Database Cleanup

You must clear the test data from the database before starting.
Run the following SQL script on the target environment database:

BEGIN TRAN;

DELETE FROM dbo.ScheduledTransfers
WHERE Earliest > '2025-12-20';   -- Deletes all lines after 20/12/2025

-- Check how many rows were deleted
SELECT @@ROWCOUNT AS RowsDeleted;

-- If correct:
COMMIT TRAN;
-- If mistake:
-- ROLLBACK TRAN;


2. File Placement

Ensure the test CSV file (e.g., Messer_Nomination_Week52.csv) is placed in the same folder as this script.

If the filename changes, make sure to update the command line arguments or rename the file to match the default.

🚀 How to Run

Install Requirements:
Ensure Python is installed, then install the necessary libraries:

pip install playwright openpyxl
playwright install chromium


Launch the Script:
Open a terminal in the script's folder and run:

python formatted_test_runner.py


Optional Arguments:

--no-prompt: Run fully automatically without asking for user feedback.

--env qa: Specify the environment (default is dev).

--csv "MyFile.csv": Specify a custom CSV path.

📋 What Will Happen

The script will perform the following steps automatically:

Import: Uploads the nominations CSV.

Baseline Check: Screenshots the Profiles graph before changes.

Confirm Slots: Confirms a set number of blue slots (Status New).

Reject Slots: Rejects a set number of blue slots.

Reporting:

Generates a folder artifacts/{Timestamp}/ with screenshots of every step.

Creates a formatted Excel report (test_report.xlsx) inside that folder containing status, notes, and screenshot paths.

📞 Maintenance

If the application logic changes or if the script needs modification (e.g., new selectors, flow changes):
Do not modify the script yourself.
👉 Contact Luca. He will update the code and generate a new runner file for you.