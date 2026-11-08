// Load the shared API contract (contract/openapi.json, generated from the FastAPI backend) and
// validate values against it. Supports the JSON Schema subset FastAPI emits for this app.
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'

export interface Schema {
  type?: string
  format?: string
  anyOf?: Schema[]
  items?: Schema
  $ref?: string
  properties?: Record<string, Schema>
  required?: string[]
  minLength?: number
  maxLength?: number
}

interface Parameter {
  name: string
  in: 'path' | 'query'
  required?: boolean
  schema: Schema
}

export interface Operation {
  parameters?: Parameter[]
  requestBody?: { content: Record<string, { schema: Schema }>; required?: boolean }
  responses: Record<string, { content?: Record<string, { schema: Schema }> }>
}

export interface OpenAPI {
  paths: Record<string, Record<string, Operation>>
  components: { schemas: Record<string, Schema> }
}

export const contract: OpenAPI = JSON.parse(
  readFileSync(resolvePath(import.meta.dirname, '../../contract/openapi.json'), 'utf-8'),
)

export function resolve(schema: Schema): Schema {
  if (!schema.$ref) return schema
  const name = schema.$ref.replace('#/components/schemas/', '')
  const target = contract.components.schemas[name]
  if (!target) throw new Error(`Unknown schema reference ${schema.$ref}`)
  return resolve(target)
}

/** Find the operation for a concrete request path such as /api/papers/3/chat. */
export function findOperation(method: string, path: string) {
  for (const [template, ops] of Object.entries(contract.paths)) {
    const names: string[] = []
    const pattern = template.replace(/\{(\w+)\}/g, (_, name: string) => {
      names.push(name)
      return '([^/]+)'
    })
    const match = new RegExp(`^${pattern}$`).exec(path)
    const operation = ops[method.toLowerCase()]
    if (match && operation) {
      const pathParams = Object.fromEntries(names.map((n, i) => [n, decodeURIComponent(match[i + 1])]))
      return { template, operation, pathParams }
    }
  }
  return null
}

/**
 * Validate a value against a schema and return a list of problems (empty when valid).
 * With `exact`, objects must contain every property the schema declares and nothing else:
 * use it for response fixtures, since the backend always serializes every field.
 * Without it, only required properties must be present and unknown keys are allowed,
 * which matches how FastAPI parses request bodies.
 */
export function validate(value: unknown, schema: Schema, exact: boolean, path = '$'): string[] {
  schema = resolve(schema)
  if (schema.anyOf) {
    const results = schema.anyOf.map((s) => validate(value, s, exact, path))
    return results.some((r) => r.length === 0) ? [] : [`${path}: matches none of ${JSON.stringify(schema.anyOf)}`]
  }
  switch (schema.type) {
    case undefined:
      return []
    case 'null':
      return value === null ? [] : [`${path}: expected null, got ${JSON.stringify(value)}`]
    case 'boolean':
      return typeof value === 'boolean' ? [] : [`${path}: expected boolean, got ${JSON.stringify(value)}`]
    case 'integer':
      return Number.isInteger(value) ? [] : [`${path}: expected integer, got ${JSON.stringify(value)}`]
    case 'number':
      return typeof value === 'number' ? [] : [`${path}: expected number, got ${JSON.stringify(value)}`]
    case 'string': {
      if (typeof value !== 'string') return [`${path}: expected string, got ${JSON.stringify(value)}`]
      const errors = []
      if (schema.format === 'date-time' && Number.isNaN(Date.parse(value))) errors.push(`${path}: not a date-time`)
      if (schema.minLength !== undefined && value.length < schema.minLength) errors.push(`${path}: too short`)
      if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}: too long`)
      return errors
    }
    case 'array':
      if (!Array.isArray(value)) return [`${path}: expected array, got ${JSON.stringify(value)}`]
      return value.flatMap((item, i) => (schema.items ? validate(item, schema.items, exact, `${path}[${i}]`) : []))
    case 'object': {
      if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        return [`${path}: expected object, got ${JSON.stringify(value)}`]
      }
      const record = value as Record<string, unknown>
      const properties = schema.properties ?? {}
      const mustHave = exact ? Object.keys(properties) : (schema.required ?? [])
      const errors = mustHave.filter((k) => !(k in record)).map((k) => `${path}.${k}: missing`)
      if (exact) {
        errors.push(...Object.keys(record).filter((k) => !(k in properties)).map((k) => `${path}.${k}: not in the API`))
      }
      for (const [key, propSchema] of Object.entries(properties)) {
        if (key in record) errors.push(...validate(record[key], propSchema, exact, `${path}.${key}`))
      }
      return errors
    }
    default:
      return [`${path}: unsupported schema type ${schema.type}`]
  }
}

/** Check a raw URL parameter string against its declared schema. */
export function validateParam(raw: string, schema: Schema, path: string): string[] {
  const s = resolve(schema)
  const types = s.anyOf ? s.anyOf.map((x) => resolve(x).type) : [s.type]
  if (types.includes('string')) return []
  if (types.includes('integer') && /^-?\d+$/.test(raw)) return []
  if (types.includes('boolean') && /^(true|false)$/.test(raw)) return []
  return [`${path}: "${raw}" is not a valid ${types.join(' | ')}`]
}
