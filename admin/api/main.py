
# Linhas anteriores inalteradas

def jard_get_mes(id):
    ...
    for semana in semanas:
        pares = jard_query("""
            SELECT p.*, 
                json_agg(json_build_object('id',f.id,'tipo',f.tipo,'url',f.url_imagem)) AS fotos
            FROM pares p
            LEFT JOIN fotos f ON f.par_id = p.id
            WHERE p.semana_id = %s 
              AND (p.ativo IS NULL OR p.ativo = true)
              AND p.data_label BETWEEN %s AND %s
            GROUP BY p.id
            ORDER BY p.codigo_a;
        """, (semana["id"], semana["data_ini"], semana["data_fim"]))

        semana["pares"] = pares
    
    return mes

# Restante do código inalterado
