{{- define "fleet-health-api.name" -}}
fleet-health-api
{{- end -}}

{{- define "fleet-health-api.labels" -}}
app: fleet-health-api
app.kubernetes.io/name: {{ include "fleet-health-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
