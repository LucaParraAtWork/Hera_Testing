import json
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import sys

def main():
    print("=== RECHERCHE DU FICHIER JSON ===")
    artifacts_dir = Path("artifacts")
    
    if not artifacts_dir.exists():
        print("Erreur : Le dossier 'artifacts' n'existe pas. Lance d'abord ton script principal.")
        sys.exit(1)

    # Trouver le run le plus récent en ignorant les dossiers système comme 'playwright_session'
    runs = sorted([d for d in artifacts_dir.iterdir() if d.is_dir() and d.name[0].isdigit()])
    if not runs:
        print("Erreur : Aucun dossier de run trouvé dans 'artifacts'.")
        sys.exit(1)
        
    latest_run = runs[-1]
    
    # Chercher dans step07_profiles/data (après les confirmations)
    data_dir = latest_run / "step07_profiles" / "data"
    if not data_dir.exists():
        print(f"Erreur : Le dossier {data_dir} n'existe pas. Vérifie que l'étape 7 a bien tourné.")
        sys.exit(1)
        
    # Trouver le fichier contenant les profils (POST sur ProfilesScreen)
    json_files = list(data_dir.glob("*004_POST_*ProfilesScreen*.json"))
    
    if not json_files:
        print(f"Erreur : Aucun fichier de profils trouvé dans {data_dir}.")
        sys.exit(1)

    file_path = json_files[0] # On prend le premier qui matche
    print(f"Fichier trouvé : {file_path}\n")

    # 1. Charger le fichier JSON
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 2. Isoler les données du graphique "Truck fillings by loading bay"
    try:
        truck_graph = next(g for g in data["data"]["profileGraphs"] if g["title"] == "Truck fillings by loading bay")
    except StopIteration:
        print("Erreur : Impossible de trouver le graphique 'Truck fillings by loading bay' dans le JSON.")
        sys.exit(1)

    # Préparation du graphique
    fig, ax = plt.subplots(figsize=(14, 6))

    print("=== CALCUL DES MASSES PAR TRAILER ===\n")

    # 3. Analyser chaque Loading Bay
    for line in truck_graph["profileGraphLines"]:
        bay_name = line.get("name")
        color = line.get("color", "#000000")
        points = line.get("points", {})
        
        # Ignorer si la baie n'a pas de nom (ex: courbes invisibles ou globales)
        if not bay_name:
            continue

        # Création d'un DataFrame pour manipuler les séries temporelles
        df = pd.DataFrame(list(points.items()), columns=["time", "flow_gs"])
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        
        # Tracer la courbe pour cette baie
        ax.plot(df["time"], df["flow_gs"], label=bay_name, color=color)

        # 4. Identifier les différents "Trailers" (les blocs continus où flow > 0)
        df["is_flowing"] = df["flow_gs"] > 0
        df["trailer_id"] = (df["is_flowing"] != df["is_flowing"].shift()).cumsum()
        
        active_flows = df[df["is_flowing"]]
        
        if not active_flows.empty:
            print(f"--- {bay_name} ---")
            
            # Grouper par ID de trailer
            for idx, (grp_id, trailer_data) in enumerate(active_flows.groupby("trailer_id"), 1):
                # Somme des (débit * 900s) / 1000
                total_mass_g = (trailer_data["flow_gs"] * 900).sum()
                total_mass_kg = total_mass_g / 1000
                
                start_time = trailer_data["time"].iloc[0].strftime("%d/%m/%Y %H:%M")
                end_time = trailer_data["time"].iloc[-1].strftime("%d/%m/%Y %H:%M")
                
                print(f"  Camion {idx} | De {start_time} à {end_time} | Masse : {total_mass_kg:,.2f} kg")
            print("")

    # 5. Finaliser et afficher le graphique
    ax.set_title("Truck Fillings by Loading Bay (Mass Calculation)")
    ax.set_ylabel("Flow rate (g/s)")
    ax.set_xlabel("Time (UTC)")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)

    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()