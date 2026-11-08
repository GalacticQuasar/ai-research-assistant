// Contract test: ties the frontend (api.ts and the test fixtures) to the backend's real API.
//
// contract/openapi.json is generated from the FastAPI app; the backend test test_contract.py fails
// whenever it is out of date. Here, every api.ts function is called with fetch mocked, and:
//   - the request (method, path, path/query params, body) must match an operation in the contract;
//   - the fixture it receives must match the operation's documented response *exactly*: every field
//     the backend returns is present and no field is unknown to the backend.
// The fixtures are typed with api.ts's interfaces (checked by `tsc -b` in `npm run build`), so
// fixture == api.ts types, and this test makes fixture == backend schema.
import { describe, expect, it, vi } from 'vitest'
import { api } from '../src/api'
import { jsonResponse, makeMessage, makePaper, makeResult } from './helpers'
import type { Operation } from './openapi'
import { contract, findOperation, resolve, validate, validateParam } from './openapi'

interface Case {
  name: keyof typeof api
  call: () => unknown
  response?: unknown // the fixture the backend would return; omitted for non-JSON endpoints
}

const allNullPaper = makePaper({
  source: 'upload', external_id: null, year: null, abstract: null, url: null, pdf_url: null, has_pdf: false,
  text_status: 'failed', summary: null,
})
const pdfFile = new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' })

const CASES: Case[] = [
  { name: 'search', call: () => api.search('graph neural nets', 5),
    response: [makeResult(), makeResult({ external_id: 'b', saved_id: 3, year: null, pdf_url: null })] },
  { name: 'listPapers', call: () => api.listPapers(), response: [makePaper(), allNullPaper] },
  { name: 'getPaper', call: () => api.getPaper(1), response: makePaper({ summary: '## TL;DR' }) },
  { name: 'savePaper', call: () => api.savePaper(makeResult()), response: makePaper({ text_status: 'pending' }) },
  { name: 'deletePaper', call: () => api.deletePaper(1) },
  { name: 'retryFetchText', call: () => api.retryFetchText(1), response: makePaper({ text_status: 'pending' }) },
  { name: 'pdfUrl', call: () => fetch(api.pdfUrl(1)) },
  { name: 'uploadPaper', call: () => api.uploadPaper(pdfFile), response: allNullPaper },
  { name: 'summarize', call: () => api.summarize(1, true), response: makePaper({ summary: 'Summary' }) },
  { name: 'getChat', call: () => api.getChat(1),
    response: [makeMessage(), makeMessage({ id: 2, role: 'assistant', content: 'Answer' })] },
  { name: 'ask', call: () => api.ask(1, 'What datasets are used?'),
    response: [makeMessage(), makeMessage({ id: 2, role: 'assistant', content: 'Answer' })] },
  { name: 'clearChat', call: () => api.clearChat(1) },
]

function successStatus(operation: Operation) {
  const status = Object.keys(operation.responses).find((s) => s.startsWith('2'))
  if (!status) throw new Error('Operation documents no success response')
  return Number(status)
}

/** Call the api function, capturing the request, and answer with the fixture. */
async function capture(c: Case) {
  let request: { url: URL; init: RequestInit } | undefined
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      request = { url: new URL(String(input), 'http://localhost'), init: init ?? {} }
      const found = findOperation(init?.method ?? 'GET', request.url.pathname)
      const status = found ? successStatus(found.operation) : 200
      return status === 204 ? new Response(null, { status }) : jsonResponse(c.response ?? {}, status)
    }),
  )
  await c.call()
  if (!request) throw new Error(`${c.name} made no request`)
  return request
}

function requestProblems(operation: Operation, pathParams: Record<string, string>, url: URL, init: RequestInit) {
  const problems: string[] = []
  const params = operation.parameters ?? []

  for (const p of params.filter((p) => p.in === 'path')) {
    problems.push(...validateParam(pathParams[p.name], p.schema, `path ${p.name}`))
  }
  const queryParams = params.filter((p) => p.in === 'query')
  for (const [key, value] of url.searchParams) {
    const declared = queryParams.find((p) => p.name === key)
    if (!declared) problems.push(`query ${key}: not accepted by the API`)
    else problems.push(...validateParam(value, declared.schema, `query ${key}`))
  }
  for (const p of queryParams.filter((p) => p.required && !url.searchParams.has(p.name))) {
    problems.push(`query ${p.name}: required but not sent`)
  }

  const content = operation.requestBody?.content
  if (!content) {
    if (init.body !== undefined) problems.push('sends a body, but the API takes none')
  } else if (content['application/json']) {
    const headers = (init.headers ?? {}) as Record<string, string>
    if (headers['Content-Type'] !== 'application/json') problems.push('JSON body without Content-Type: application/json')
    problems.push(...validate(JSON.parse(init.body as string), content['application/json'].schema, false, 'body'))
  } else if (content['multipart/form-data']) {
    if (!(init.body instanceof FormData)) return [...problems, 'expected a multipart FormData body']
    const schema = resolve(content['multipart/form-data'].schema)
    const sent = [...init.body.keys()]
    for (const key of schema.required ?? []) if (!sent.includes(key)) problems.push(`form field ${key}: missing`)
    for (const key of sent) if (!(key in (schema.properties ?? {}))) problems.push(`form field ${key}: not accepted`)
  }
  return problems
}

describe('frontend/backend API contract', () => {
  it('has a contract case for every api.ts function', () => {
    expect(CASES.map((c) => c.name).sort()).toEqual(Object.keys(api).sort())
  })

  it.each(CASES)('$name sends a request the backend accepts', async (c) => {
    const { url, init } = await capture(c)
    const method = init.method ?? 'GET'

    const found = findOperation(method, url.pathname)
    expect(found, `${method} ${url.pathname} is not an API route`).not.toBeNull()
    expect(requestProblems(found!.operation, found!.pathParams, url, init)).toEqual([])
  })

  it.each(CASES.filter((c) => c.response !== undefined))('$name fixture matches the backend response', async (c) => {
    const { url, init } = await capture(c)
    const { operation } = findOperation(init.method ?? 'GET', url.pathname)!

    const schema = operation.responses[String(successStatus(operation))].content?.['application/json']?.schema
    expect(schema, 'the API documents no JSON response here').toBeDefined()
    expect(validate(c.response, schema!, true)).toEqual([])
  })

  it('validation errors carry the msg field api.ts displays', () => {
    const detail = resolve(contract.components.schemas.HTTPValidationError.properties!.detail)
    const item = resolve(detail.items!)
    expect(item.required).toContain('msg')
    expect(resolve(item.properties!.msg).type).toBe('string')
  })
})

describe('contract validator self-check', () => {
  // Guards against a validator that accepts everything, which would make the tests above vacuous
  const paperSchema = findOperation('GET', '/api/papers/1')!.operation.responses['200'].content!['application/json'].schema

  it('rejects a renamed field', () => {
    const { has_pdf, ...rest } = makePaper()
    expect(validate({ ...rest, hasPdf: has_pdf }, paperSchema, true)).toEqual([
      '$.has_pdf: missing',
      '$.hasPdf: not in the API',
    ])
  })

  it('rejects a wrong type and a missing nullable field', () => {
    const { summary: _summary, ...rest } = makePaper({ year: '2024' as unknown as number })
    const problems = validate(rest, paperSchema, true)
    expect(problems).toContain('$.summary: missing')
    expect(problems.some((p) => p.startsWith('$.year'))).toBe(true)
  })

  it('rejects an unknown route', () => {
    expect(findOperation('GET', '/api/paper/1')).toBeNull()
    expect(findOperation('PUT', '/api/papers/1')).toBeNull()
  })
})
