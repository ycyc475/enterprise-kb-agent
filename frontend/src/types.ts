export interface Profile {
  display_name: string;
  department: string;
  grade: "S1" | "S2";
  revision: number;
}
export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  revision: number;
}
export interface User {
  id: string;
  username: string;
  role: "admin" | "employee";
  is_active: boolean;
  must_change_password: boolean;
  profile: Profile | null;
  knowledge_bases?: KnowledgeBase[];
}
export interface TokenData {
  access_token: string;
  token_type: string;
  expires_in: number;
  user?: User;
}
export interface Page<T> {
  items: T[];
  total: number;
}
