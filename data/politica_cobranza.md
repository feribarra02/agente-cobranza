# Política de cobranza preventiva y temprana (v3, vigente desde marzo 2025)

**Alcance.** Aplica a todas las cuentas de crédito con pagos vencidos de la financiera. Es una política simplificada para el ejercicio.

## 1. Cálculo del atraso
Los días de atraso se cuentan desde la fecha de vencimiento de la mensualidad más antigua sin pagar. El campo `dias_atraso` del sistema es la fuente oficial y se actualiza todas las noches.

## 2. Acción según los días de atraso
| Días de atraso | Acción |
| --- | --- |
| 1 a 7 | Recordatorio amistoso por SMS |
| 8 a 30 | Recordatorio firme por SMS, con opciones de pago |
| 31 a 60 | Recordatorio formal por SMS y llamada del cobrador |
| Más de 60 | Se escala al gerente de cobranza |

## 3. Trato a clientes con buen historial
A los clientes con buen historial de pago se les dará un trato más flexible.

## 4. Promesas de pago
Cuando el cliente registra una promesa de pago, se debe esperar un tiempo razonable antes de volver a contactarlo. Si la promesa no se cumple, se contacta de nuevo. Dos o más promesas incumplidas se escalan al gerente.

## 5. Contenido de los mensajes
Todo mensaje debe incluir el nombre del cliente, el monto vencido, la fecha de vencimiento y el medio de pago disponible. Nunca se menciona la deuda a terceros. El mensaje no debe contener errores en el monto.

## 6. Registro
Cada contacto se registra en el campo `fecha_ultimo_contacto`.
