import streamlit as st
from Common.configuration import Configuration as tec_conf

# Configurazione base della pagina
st.set_page_config(page_title="Nutriflow", page_icon="🍏", layout="wide")

st.session_state.tec_conf = tec_conf

st.title("Benvenuto in Nutriflow 🍏")
st.markdown("Seleziona un modulo dalla barra laterale per iniziare.")

# Inizializzazione dello stato globale
if 'current_patient_id' not in st.session_state:
    st.session_state.current_patient_id = None
    
if 'current_patient_name' not in st.session_state:
    st.session_state.current_patient_name = None

# if st.session_state.current_patient_id:
#     st.success(f"Paziente attualmente in sessione: **{st.session_state.current_patient_name}**")
# else:
#     st.info("Nessun paziente selezionato. Vai nella sezione Pazienti per sceglierne o aggiungerne uno.")