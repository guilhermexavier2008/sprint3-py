import csv
import json
import os
import random
import re
import sys
from datetime import datetime

# ---------------------------------------------------------------- parâmetros
PICO_SOLAR_KW = 10.0        
POTENCIA_CARREGADOR_KW = 7.4
EFICIENCIA = 0.92          
PASSO_MIN = 5               
LIMITE_TEMP_C = 45.0        
FATOR_CO2_KG_KWH = 0.08     
PASTA_DADOS = "dados"

MODELOS = [
    ("Nissan Leaf", 40.0), ("BYD Dolphin", 44.9), ("Renault Zoe", 52.0),
    ("Chevrolet Bolt", 65.0), ("BYD Dolphin Mini", 30.1), ("JAC E-JS1", 30.2),
]


# ---------------------------------------------------------------- classes
class Sessao:
    """Uma sessão de recarga completa."""

    def __init__(self, id_, placa, modelo, capacidade_kwh, soc_inicial, soc_final,
                 hora_inicio, duracao_min, energia_solar, energia_rede, temp_max):
        self.id = id_
        self.placa = placa
        self.modelo = modelo
        self.capacidade_kwh = capacidade_kwh
        self.soc_inicial = soc_inicial
        self.soc_final = soc_final
        self.hora_inicio = hora_inicio
        self.duracao_min = duracao_min
        self.energia_solar = energia_solar
        self.energia_rede = energia_rede
        self.temp_max = temp_max
        self.leituras = []          # lista de dicts (uma por leitura de sensor)

    @property
    def energia_total(self):
        return self.energia_solar + self.energia_rede

    @property
    def percentual_solar(self):
        if self.energia_total == 0:
            return 0.0
        return self.energia_solar / self.energia_total * 100

    @property
    def co2_evitado(self):
        return self.energia_solar * FATOR_CO2_KG_KWH

    def para_dict(self):
        return {
            "id": self.id, "placa": self.placa, "modelo": self.modelo,
            "capacidade_kwh": self.capacidade_kwh,
            "soc_inicial": round(self.soc_inicial, 1),
            "soc_final": round(self.soc_final, 1),
            "hora_inicio": self.hora_inicio, "duracao_min": self.duracao_min,
            "energia_solar_kwh": round(self.energia_solar, 2),
            "energia_rede_kwh": round(self.energia_rede, 2),
            "energia_total_kwh": round(self.energia_total, 2),
            "percentual_solar": round(self.percentual_solar, 1),
            "co2_evitado_kg": round(self.co2_evitado, 2),
            "temp_max_c": round(self.temp_max, 1),
        }

    def __str__(self):
        return (f"#{self.id:<3} {self.placa:<8} {self.modelo:<17} "
                f"{self.soc_inicial:>5.1f}%->{self.soc_final:>5.1f}%  "
                f"{self.hora_inicio:>2}h  {self.duracao_min:>4} min  "
                f"{self.energia_total:>6.2f} kWh  ({self.percentual_solar:>5.1f}% solar)")


class Estacao:
    """Controlador da estação: simula solar, sensores e decide os comandos."""

    def __init__(self):
        self.sessoes = []
        self.log_comandos = []      # lista de dicts
        self.proximo_id = 1

    @staticmethod
    def potencia_solar(hora):
        """Geração do painel (kW) em função da hora do dia, com nuvens aleatórias."""
        if hora < 6 or hora > 18:
            return 0.0
        import math
        base = PICO_SOLAR_KW * math.sin(math.pi * (hora - 6) / 12)
        return max(0.0, base * random.uniform(0.75, 1.0))

    def _registrar_comando(self, id_sessao, minuto, comando, motivo):
        self.log_comandos.append({
            "sessao": id_sessao, "minuto": minuto, "comando": comando, "motivo": motivo
        })

    def simular_sessao(self, placa, modelo, capacidade, soc_ini, soc_meta, hora_inicio):
        """Roda a recarga passo a passo e devolve a Sessao já registrada."""
        id_ = self.proximo_id
        self.proximo_id += 1

        soc = soc_ini
        minuto = 0
        ambiente = random.uniform(22, 35)
        temp = ambiente
        temp_max = temp
        energia_solar = 0.0
        energia_rede = 0.0
        leituras = []
        comando_atual = None
        limitando = False           # proteção térmica com histerese (evita liga/desliga a cada passo)

        while soc < soc_meta and minuto < 24 * 60:
            hora = (hora_inicio + minuto / 60) % 24
            solar = self.potencia_solar(hora)

            # ---- lógica de automação: escolhe a potência conforme o sol
            if solar >= POTENCIA_CARREGADOR_KW:
                pot, comando, motivo = POTENCIA_CARREGADOR_KW, "SOLAR_TOTAL", "sol cobre toda a recarga"
            elif solar > 0.5:
                pot, comando, motivo = POTENCIA_CARREGADOR_KW, "HIBRIDO_SOLAR_REDE", "sol parcial, rede complementa"
            else:
                pot, comando, motivo = POTENCIA_CARREGADOR_KW * 0.5, "REDE_POTENCIA_REDUZIDA", "sem sol, recarga mais leve na rede"

            if soc >= 80:               # fase final da bateria carrega mais devagar
                pot *= 0.6
            if temp > LIMITE_TEMP_C:
                limitando = True
            elif temp < LIMITE_TEMP_C - 4:
                limitando = False
            if limitando:               # proteção térmica tem prioridade
                pot *= 0.5
                comando, motivo = "REDUCAO_TERMICA", f"temperatura acima de {LIMITE_TEMP_C:.0f} C"

            if comando != comando_atual:    # só registra quando o comando muda
                self._registrar_comando(id_, minuto, comando, motivo)
                comando_atual = comando

            # ---- energia do passo (não passa da meta de carga)
            e_passo = pot * PASSO_MIN / 60
            faltante = (soc_meta - soc) / 100 * capacidade / EFICIENCIA
            e_passo = min(e_passo, faltante)
            fracao_solar = min(1.0, solar / pot) if pot > 0 else 0.0
            energia_solar += e_passo * fracao_solar
            energia_rede += e_passo * (1 - fracao_solar)
            soc += e_passo * EFICIENCIA / capacidade * 100

            # ---- sensor de temperatura simulado
            temp += pot * 0.2 - (temp - ambiente) * 0.08 + random.uniform(-0.3, 0.3)
            temp_max = max(temp_max, temp)

            minuto += PASSO_MIN
            leituras.append({
                "sessao": id_, "minuto": minuto, "hora": round(hora, 2),
                "solar_kw": round(solar, 2), "potencia_kw": round(pot, 2),
                "temp_c": round(temp, 1), "soc": round(soc, 1), "comando": comando,
            })

        sessao = Sessao(id_, placa, modelo, capacidade, soc_ini, soc, int(hora_inicio),
                        minuto, energia_solar, energia_rede, temp_max)
        sessao.leituras = leituras
        self.sessoes.append(sessao)
        return sessao


# ---------------------------------------------------------------- algoritmos manuais
def bubble_sort(lista, chave, reverso=False):
    v = list(lista)
    n = len(v)
    for i in range(n - 1):
        trocou = False
        for j in range(n - 1 - i):
            a, b = chave(v[j]), chave(v[j + 1])
            if (a < b) if reverso else (a > b):
                v[j], v[j + 1] = v[j + 1], v[j]
                trocou = True
        if not trocou:
            break
    return v


def selection_sort(lista, chave, reverso=False):
    v = list(lista)
    n = len(v)
    for i in range(n - 1):
        escolhido = i
        for j in range(i + 1, n):
            a, b = chave(v[j]), chave(v[escolhido])
            if (a > b) if reverso else (a < b):
                escolhido = j
        v[i], v[escolhido] = v[escolhido], v[i]
    return v


def insertion_sort(lista, chave, reverso=False):
    v = list(lista)
    for i in range(1, len(v)):
        atual = v[i]
        j = i - 1
        while j >= 0 and ((chave(v[j]) < chave(atual)) if reverso else (chave(v[j]) > chave(atual))):
            v[j + 1] = v[j]
            j -= 1
        v[j + 1] = atual
    return v


def busca_linear_placa(lista, placa):
    """Percorre tudo e devolve todas as sessões daquela placa. O(n)."""
    achadas = []
    for s in lista:
        if s.placa == placa:
            achadas.append(s)
    return achadas


def busca_binaria_id(lista_ordenada_por_id, id_):
    """Busca binária por id. A lista PRECISA estar ordenada por id. O(log n)."""
    inicio, fim = 0, len(lista_ordenada_por_id) - 1
    while inicio <= fim:
        meio = (inicio + fim) // 2
        atual = lista_ordenada_por_id[meio].id
        if atual == id_:
            return lista_ordenada_por_id[meio]
        if atual < id_:
            inicio = meio + 1
        else:
            fim = meio - 1
    return None


# ---------------------------------------------------------------- estatísticas
def calcular_estatisticas(sessoes):
    if len(sessoes) == 0:
        return None
    total = solar = rede = duracao = co2 = 0.0
    maior = menor = sessoes[0]
    for s in sessoes:
        total += s.energia_total
        solar += s.energia_solar
        rede += s.energia_rede
        duracao += s.duracao_min
        co2 += s.co2_evitado
        if s.energia_total > maior.energia_total:
            maior = s
        if s.energia_total < menor.energia_total:
            menor = s
    n = len(sessoes)
    return {
        "quantidade": n, "energia_total": total, "energia_solar": solar,
        "energia_rede": rede, "media_energia": total / n, "duracao_media": duracao / n,
        "percentual_solar": solar / total * 100 if total else 0.0,
        "co2_evitado": co2, "maior": maior, "menor": menor,
    }


def mostrar_estatisticas(sessoes):
    e = calcular_estatisticas(sessoes)
    if e is None:
        print("\nNenhuma sessão registrada ainda.")
        return
    print("\n===== ESTATÍSTICAS DA ESTAÇÃO =====")
    print(f"Sessões realizadas ....... {e['quantidade']}")
    print(f"Energia total ............ {e['energia_total']:.2f} kWh")
    print(f"  - vinda do sol ......... {e['energia_solar']:.2f} kWh ({e['percentual_solar']:.1f}%)")
    print(f"  - vinda da rede ........ {e['energia_rede']:.2f} kWh")
    print(f"Média por sessão ......... {e['media_energia']:.2f} kWh")
    print(f"Duração média ............ {e['duracao_media']:.0f} min")
    print(f"Maior sessão ............. #{e['maior'].id} ({e['maior'].energia_total:.2f} kWh)")
    print(f"Menor sessão ............. #{e['menor'].id} ({e['menor'].energia_total:.2f} kWh)")
    print(f"CO2 evitado (estimado) ... {e['co2_evitado']:.2f} kg")


# ---------------------------------------------------------------- entrada validada
def ler_texto(msg):
    while True:
        t = input(msg).strip()
        if t:
            return t
        print("  Campo vazio, tente de novo.")


def ler_inteiro(msg, minimo, maximo):
    while True:
        try:
            v = int(input(msg).strip())
        except ValueError:
            print("  Digite um número inteiro.")
            continue
        if minimo <= v <= maximo:
            return v
        print(f"  Valor deve estar entre {minimo} e {maximo}.")


def ler_decimal(msg, minimo, maximo):
    while True:
        try:
            v = float(input(msg).strip().replace(",", "."))
        except ValueError:
            print("  Digite um número (ex.: 45.5).")
            continue
        if minimo <= v <= maximo:
            return v
        print(f"  Valor deve estar entre {minimo} e {maximo}.")


def ler_placa(msg):
    while True:
        p = input(msg).strip().upper().replace("-", "")
        if re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]\d{2}", p):
            return p
        print("  Placa inválida. Exemplos: ABC1D23 ou ABC-1234.")


# ---------------------------------------------------------------- exportação
def exportar_dados(estacao):
    if not estacao.sessoes:
        print("\nNada para exportar ainda.")
        return
    os.makedirs(PASTA_DADOS, exist_ok=True)
    dicts = [s.para_dict() for s in estacao.sessoes]

    with open(os.path.join(PASTA_DADOS, "sessoes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dicts[0].keys()))
        w.writeheader()
        w.writerows(dicts)

    with open(os.path.join(PASTA_DADOS, "sessoes.json"), "w", encoding="utf-8") as f:
        json.dump(dicts, f, ensure_ascii=False, indent=2)

    leituras = [l for s in estacao.sessoes for l in s.leituras]
    with open(os.path.join(PASTA_DADOS, "leituras.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(leituras[0].keys()))
        w.writeheader()
        w.writerows(leituras)

    if estacao.log_comandos:
        with open(os.path.join(PASTA_DADOS, "comandos.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(estacao.log_comandos[0].keys()))
            w.writeheader()
            w.writerows(estacao.log_comandos)

    print(f"\nArquivos gerados na pasta '{PASTA_DADOS}/': "
          "sessoes.csv, sessoes.json, leituras.csv, comandos.csv")


# ---------------------------------------------------------------- telas do menu
def cabecalho_lista():
    print(f"{'ID':<4} {'PLACA':<8} {'MODELO':<17} {'BATERIA':<16} {'HORA':<5} "
          f"{'DURAÇÃO':<9} {'ENERGIA':<10} SOLAR")


def listar(sessoes, titulo="SESSÕES REGISTRADAS"):
    if not sessoes:
        print("\nNenhuma sessão para mostrar.")
        return
    print(f"\n===== {titulo} =====")
    cabecalho_lista()
    for s in sessoes:
        print(s)


def nova_sessao(estacao):
    print("\n--- Nova sessão de recarga ---")
    placa = ler_placa("Placa do veículo: ")
    print("Modelos disponíveis:")
    for i, (nome, cap) in enumerate(MODELOS, start=1):
        print(f"  {i}) {nome} ({cap} kWh)")
    escolha = ler_inteiro("Escolha o modelo: ", 1, len(MODELOS))
    modelo, capacidade = MODELOS[escolha - 1]
    soc_ini = ler_decimal("Bateria atual (%): ", 0, 99)
    soc_meta = ler_decimal("Bateria desejada (%): ", soc_ini + 1, 100)
    hora = ler_inteiro("Hora de início (0 a 23): ", 0, 23)

    s = estacao.simular_sessao(placa, modelo, capacidade, soc_ini, soc_meta, hora)
    print("\nRecarga concluída!")
    detalhar(s, estacao)


def detalhar(s, estacao):
    print(f"\n===== SESSÃO #{s.id} - {s.placa} ({s.modelo}) =====")
    print(f"Bateria: {s.soc_inicial:.1f}% -> {s.soc_final:.1f}%  |  Duração: {s.duracao_min} min")
    print(f"Energia: {s.energia_total:.2f} kWh "
          f"(solar {s.energia_solar:.2f} + rede {s.energia_rede:.2f}) -> {s.percentual_solar:.1f}% solar")
    print(f"CO2 evitado (estimado): {s.co2_evitado:.2f} kg  |  Temperatura máxima: {s.temp_max:.1f} C")

    print("\nComandos automatizados nesta sessão:")
    for c in estacao.log_comandos:
        if c["sessao"] == s.id:
            print(f"  min {c['minuto']:>4}: {c['comando']:<24} ({c['motivo']})")

    print("\nÚltimas leituras dos sensores:")
    print(f"  {'min':>4} {'solar kW':>9} {'pot kW':>7} {'temp C':>7} {'bateria':>8}")
    for l in s.leituras[-5:]:
        print(f"  {l['minuto']:>4} {l['solar_kw']:>9} {l['potencia_kw']:>7} "
              f"{l['temp_c']:>7} {l['soc']:>7}%")


def menu_busca(estacao):
    if not estacao.sessoes:
        print("\nNenhuma sessão registrada ainda.")
        return
    print("\n1) Buscar por placa (busca linear)")
    print("2) Buscar por ID (busca binária)")
    op = ler_inteiro("Escolha: ", 1, 2)
    if op == 1:
        placa = ler_placa("Placa: ")
        achadas = busca_linear_placa(estacao.sessoes, placa)
        if achadas:
            listar(achadas, f"RESULTADO PARA {placa}")
        else:
            print("Nenhuma sessão encontrada para essa placa.")
    else:
        id_ = ler_inteiro("ID da sessão: ", 1, 10**6)
        ordenada = insertion_sort(estacao.sessoes, lambda s: s.id)   # garante a ordem exigida
        s = busca_binaria_id(ordenada, id_)
        if s:
            detalhar(s, estacao)
        else:
            print("Sessão não encontrada.")


def menu_ordenar(estacao):
    if not estacao.sessoes:
        print("\nNenhuma sessão registrada ainda.")
        return
    campos = {
        1: ("energia total", lambda s: s.energia_total),
        2: ("duração", lambda s: s.duracao_min),
        3: ("% de energia solar", lambda s: s.percentual_solar),
        4: ("placa", lambda s: s.placa),
        5: ("hora de início", lambda s: s.hora_inicio),
    }
    print("\nOrdenar por:")
    for k, (nome, _) in campos.items():
        print(f"  {k}) {nome}")
    campo = ler_inteiro("Campo: ", 1, len(campos))
    print("Algoritmo: 1) Bubble  2) Selection  3) Insertion")
    alg = ler_inteiro("Algoritmo: ", 1, 3)
    reverso = ler_inteiro("Ordem: 1) crescente  2) decrescente: ", 1, 2) == 2

    funcao = {1: bubble_sort, 2: selection_sort, 3: insertion_sort}[alg]
    resultado = funcao(estacao.sessoes, campos[campo][1], reverso)
    listar(resultado, f"ORDENADO POR {campos[campo][0].upper()}")


def mostrar_comandos(estacao):
    if not estacao.log_comandos:
        print("\nNenhum comando automatizado registrado ainda.")
        return
    print("\n===== LOG DE COMANDOS AUTOMATIZADOS =====")
    for c in estacao.log_comandos:
        print(f"sessão #{c['sessao']:<3} min {c['minuto']:>4}: {c['comando']:<24} ({c['motivo']})")


def gerar_demo(estacao, quantidade=8):
    """Cria sessões aleatórias para popular o sistema (bom para o vídeo)."""
    for _ in range(quantidade):
        letras = "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(3))
        placa = f"{letras}{random.randint(0, 9)}{random.choice('ABCDEFGHJ')}{random.randint(10, 99)}"
        modelo, cap = random.choice(MODELOS)
        soc_ini = random.randint(5, 50)
        soc_meta = random.choice([80, 90, 100])
        hora = random.randint(0, 23)
        estacao.simular_sessao(placa, modelo, cap, soc_ini, soc_meta, hora)
    print(f"\n{quantidade} sessões de demonstração geradas.")


# ---------------------------------------------------------------- programa principal
def menu():
    estacao = Estacao()
    while True:
        print("\n========== ESTAÇÃO DE RECARGA INTELIGENTE ==========")
        print("1) Nova sessão de recarga")
        print("2) Listar sessões")
        print("3) Buscar sessão")
        print("4) Ordenar sessões")
        print("5) Estatísticas")
        print("6) Log de comandos automatizados")
        print("7) Gerar sessões de demonstração")
        print("8) Exportar dados (CSV/JSON)")
        print("0) Sair")
        op = ler_inteiro("Opção: ", 0, 8)

        if op == 1:
            nova_sessao(estacao)
        elif op == 2:
            listar(estacao.sessoes)
        elif op == 3:
            menu_busca(estacao)
        elif op == 4:
            menu_ordenar(estacao)
        elif op == 5:
            mostrar_estatisticas(estacao.sessoes)
        elif op == 6:
            mostrar_comandos(estacao)
        elif op == 7:
            qtd = ler_inteiro("Quantas sessões? (1 a 50): ", 1, 50)
            gerar_demo(estacao, qtd)
        elif op == 8:
            exportar_dados(estacao)
        else:
            print("Encerrando. Até mais!")
            break


def main():
    if "--demo" in sys.argv:
        random.seed(42)                     # mesmo resultado sempre, bom para o vídeo
        estacao = Estacao()
        gerar_demo(estacao, 8)
        listar(estacao.sessoes)
        mostrar_estatisticas(estacao.sessoes)
        mostrar_comandos(estacao)
        exportar_dados(estacao)
        return
    menu()


if __name__ == "__main__":
    main()
