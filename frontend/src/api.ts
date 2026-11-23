export async function send(path: string, options?: RequestInit): Promise<Response> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (typeof payload?.detail === 'string') throw new Error(payload.detail);
    if (response.status === 422) throw new Error('Check the values you entered and try again.');
    throw new Error('Check that the local API is running and try again.');
  }
  return response;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  return (await send(path, options)).json();
}
