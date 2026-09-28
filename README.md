# Spotter Backend Assessment - Fuel Route Optimizer

A production-minded Django REST API that computes driving routes across the USA, identifies cost-effective fuel stops, and calculates fuel costs based on vehicle range and efficiency constraints.

## Requirements Summary

- Vehicle maximum range: 500 miles
- Fuel efficiency: 10 MPG (50 gallon tank capacity)
- Starting fuel: Full tank (50 gallons) at $0 cost
- Objective: Minimize total money spent on fuel
- Minimized external API calls via caching and local preprocessing
- Framework: Latest stable Django + Django REST Framework

## Quickstart

```bash
# Activate virtual environment
.venv\Scripts\activate   # Windows
# source .venv/bin/activate # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Check system
python manage.py check

# Run tests
python manage.py test
```
