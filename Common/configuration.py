import os
from dotenv import load_dotenv


load_dotenv()

class Configuration():
    DATABASE_URL = os.getenv("DATABASE_URL")
    # Mappatura dei giorni della settimana
    GIORNI_SETTIMANA = {
        1: "Lunedì", 2: "Martedì", 3: "Mercoledì", 
        4: "Giovedì", 5: "Venerdì", 6: "Sabato", 7: "Domenica"
    }