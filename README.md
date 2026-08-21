# TdTWorld EPG

Publicación automatizada de guías EPG compactas, separadas por país, para
TdTWorld. Este repositorio contiene únicamente la configuración y las
herramientas necesarias para generar los datos EPG; el código de la aplicación
permanece en un repositorio privado independiente.

## Países publicados

- Alemania (`de`)
- España (`es`)
- Francia (`fr`)
- Italia (`it`)
- Portugal (`pt`)
- Reino Unido (`uk`)

Cada país se genera y distribuye de forma independiente en la rama `epg-data`:

```text
epg/<country>/epg.bin.gz
epg/<country>/guide.xml.gz
epg/<country>/fanart.bin.gz
epg/<country>/version.json
```

La aplicación consulta `version.json` y solo descarga el EPG y el manifiesto de
fanart del país activo. El manifiesto de imágenes es independiente para que su
descarga y precarga se hagan después de mostrar la pantalla principal.
Al cambiar de país descarga el correspondiente y lo conserva para el siguiente
arranque.

## Actualización

El workflow `Publish compact EPG` se ejecuta cada seis horas y también admite
ejecución manual. Primero valida que existan todos los canales configurados y
solo después reemplaza la rama de datos, preservando así el último conjunto
válido si una fuente externa falla.
