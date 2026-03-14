#!/bin/bash

# Activate virtual environment
source /Users/prashantmalviya/codebase/RLAI_Titan/birthday_campaign/tvenv1/bin/activate

# Move to project directory
cd /Users/prashantmalviya/codebase/RLAI_Titan/birthday_campaign

# Run PROD script
python generate_messages_prod.py >> logs/birthday_campaign.log 2>&1
